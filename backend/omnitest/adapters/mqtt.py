"""MQTT 3.1.1 actions with scoped topics, bounded queues and acknowledged operations."""

import asyncio
import json
import ssl
import threading
from typing import Any, Literal
from uuid import uuid4

import paho.mqtt.client as mqtt
from pydantic import Field, model_validator

from omnitest.core.ir import Model
from omnitest.core.security import check_url, render

from .base import AdapterInfo


class Endpoint(Model):
    host: str = Field(min_length=1)
    port: int = Field(default=1883, ge=1, le=65535)
    tls: bool = False
    topic_prefix: str = Field(min_length=2, max_length=400)
    allow_publish: bool = False
    allow_retained: bool = False
    allow_wildcards: bool = False
    username: str | None = Field(default=None, max_length=200)
    password_secret: str | None = Field(default=None, pattern=r"^OMNITEST_SECRET_[A-Z0-9_]+$")
    connect_timeout: float = Field(default=5, ge=0.1, le=15)

    @model_validator(mode="after")
    def scope(self):
        if not self.topic_prefix.endswith("/") or any(c in self.topic_prefix for c in "+#\x00"):
            raise ValueError("MQTT topic_prefix must be a literal scope ending in /")
        if self.topic_prefix.startswith("$SYS/"):
            raise ValueError("Broker system topics are not test scopes")
        if self.password_secret and not self.username:
            raise ValueError("MQTT password reference requires a username")
        return self


class Subscription(Model):
    topic: str = Field(min_length=1, max_length=512)
    qos: Literal[0, 1] = 1
    timeout: float = Field(default=5, ge=0.05, le=30)


class Publish(Subscription):
    text: str | None = None
    hex: str | None = None
    json_body: Any = Field(default=None, alias="json")
    retain: bool = False


class Receive(Model):
    timeout: float = Field(default=5, ge=0.05, le=30)


class MqttAdapter:
    info = AdapterInfo(
        "mqtt",
        "1.0",
        ["subscribe", "publish", "receive", "qos0", "qos1", "retained", "tls"],
        ["authorized MQTT 3.1.1 broker and topic scope"],
        ["mqtt"],
    )

    async def check(self, context):
        self.cfg = Endpoint.model_validate(context.target["config"])
        cfg = self.cfg
        host = f"[{cfg.host}]" if ":" in cfg.host else cfg.host
        check_url(f"http://{host}:{cfg.port}", context.settings.allowed_hosts)
        self.loop = asyncio.get_running_loop()
        self.failed, self.error = asyncio.Event(), None
        self.messages = asyncio.Queue(maxsize=100)
        self.message_slots = threading.BoundedSemaphore(100)
        self.pending = {}
        self.subscribed = False
        self.closing = False
        connected = self.loop.create_future()
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="omnitest-" + uuid4().hex[:20],
            clean_session=True,
            protocol=mqtt.MQTTv311,
            reconnect_on_failure=False,
        )
        self.client.connect_timeout = cfg.connect_timeout
        self.client.max_queued_messages_set(100)
        self.client.max_inflight_messages_set(4)
        if cfg.username:
            username = render(cfg.username, context.variables, context.allowed_secrets)
            password = (
                render("${secret." + cfg.password_secret + "}", {}, context.allowed_secrets)
                if cfg.password_secret
                else None
            )
            self.client.username_pw_set(username, password)
        if cfg.tls:
            self.client.tls_set_context(ssl.create_default_context())

        def dispatch(callback, *args):
            if not self.closing:
                self.loop.call_soon_threadsafe(callback, *args)

        def on_connect(client, userdata, flags, reason, properties):
            dispatch(self._connected, connected, reason)

        def on_disconnect(client, userdata, flags, reason, properties):
            dispatch(self._fail, f"MQTT broker disconnected ({reason})")

        self.client.on_connect = on_connect
        self.client.on_connect_fail = lambda *args: dispatch(self._fail, "MQTT connection failed")
        self.client.on_disconnect = on_disconnect
        self.client.on_subscribe = lambda client, userdata, mid, reasons, properties: dispatch(
            self._ack, ("subscribe", mid), reasons
        )
        self.client.on_publish = lambda client, userdata, mid, reason, properties: dispatch(
            self._ack, ("publish", mid), [reason]
        )

        def on_message(client, userdata, message):
            if self.closing:
                return
            if len(message.payload) > 65536 or not self.message_slots.acquire(blocking=False):
                dispatch(self._fail, "MQTT receive buffer or message size limit exceeded")
                client.disconnect()
                return
            dispatch(self._message, message)

        self.client.on_message = on_message
        self.client.connect_async(cfg.host, cfg.port, keepalive=30)
        self.client.loop_start()
        await self._wait(connected, cfg.connect_timeout + 1)

    def _connected(self, future, reason):
        if reason.is_failure:
            self._fail(f"MQTT broker rejected connection ({reason})")
        elif not future.done():
            future.set_result(True)

    def _fail(self, error):
        if not self.closing and not self.failed.is_set():
            self.error = error
            self.failed.set()

    def _ack(self, key, reasons):
        future = self.pending.pop(key, None)
        if future is not None and not future.done():
            if any(reason.is_failure for reason in reasons):
                future.set_exception(ValueError("MQTT broker rejected operation"))
            else:
                future.set_result([reason.value for reason in reasons])

    def _message(self, message):
        if len(message.payload) > 65536 or self.messages.full():
            self._fail("MQTT receive buffer or message size limit exceeded")
            self.client.disconnect()
            self.message_slots.release()
            return
        if not message.topic.startswith(self.cfg.topic_prefix):
            self._fail("MQTT message escapes target topic scope")
            self.message_slots.release()
            return
        self.messages.put_nowait(message)

    async def _wait(self, future, timeout):
        failure = asyncio.create_task(self.failed.wait())
        try:
            async with asyncio.timeout(timeout):
                done, _ = await asyncio.wait([future, failure], return_when=asyncio.FIRST_COMPLETED)
                if self.failed.is_set():
                    raise ValueError(self.error)
                if future in done:
                    return future.result()
        except TimeoutError as exc:
            raise ValueError(f"MQTT operation timed out after {timeout}s") from exc
        finally:
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()
            failure.cancel()
            await asyncio.gather(failure, return_exceptions=True)

    def _topic(self, topic, subscribe=False):
        if (
            not topic.startswith(self.cfg.topic_prefix)
            or "\x00" in topic
            or len(topic.encode()) > 512
        ):
            raise ValueError("MQTT topic escapes authorized prefix or size limit")
        if any(c in topic for c in "+#") and (not subscribe or not self.cfg.allow_wildcards):
            raise ValueError("MQTT wildcard topics require explicit subscription authorization")

    async def execute(self, step, context):
        if self.failed.is_set():
            raise ValueError(self.error)
        if step.action == "receive":
            cfg = Receive.model_validate(step.config)
            if not self.subscribed:
                raise ValueError("Subscribe before receiving MQTT messages")
            message = await self._wait(asyncio.create_task(self.messages.get()), cfg.timeout)
            self.message_slots.release()
            text = message.payload.decode("utf-8", errors="replace")
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            return {
                "topic": message.topic,
                "text": text,
                "hex": message.payload.hex(),
                "json": parsed,
                "bytes_received": len(message.payload),
                "qos": message.qos,
                "retained": message.retain,
            }
        if step.action == "subscribe":
            cfg = Subscription.model_validate(step.config)
            self._topic(cfg.topic, subscribe=True)
            code, mid = self.client.subscribe(cfg.topic, cfg.qos)
            kind = "subscribe"
        elif step.action == "publish":
            cfg = Publish.model_validate(step.config)
            self._topic(cfg.topic)
            if not self.cfg.allow_publish or cfg.retain and not self.cfg.allow_retained:
                raise ValueError("MQTT publish or retained write is not authorized")
            selected = cfg.model_fields_set.intersection({"text", "hex", "json_body"})
            if len(selected) != 1:
                raise ValueError("Provide exactly one MQTT payload: text, hex or json")
            if "text" in selected:
                payload = cfg.text.encode("utf-8") if cfg.text is not None else b""
            elif "hex" in selected:
                payload = bytes.fromhex(cfg.hex or "")
            else:
                payload = json.dumps(cfg.json_body, ensure_ascii=False, allow_nan=False).encode(
                    "utf-8"
                )
            if len(payload) > 65536:
                raise ValueError("MQTT payload exceeds 64 KB")
            info = self.client.publish(cfg.topic, payload, qos=cfg.qos, retain=cfg.retain)
            code, mid, kind = info.rc, info.mid, "publish"
        else:
            raise ValueError("MQTT adapter supports subscribe, publish and receive")
        if code != mqtt.MQTT_ERR_SUCCESS:
            raise ValueError("MQTT client could not submit operation")
        future = self.loop.create_future()
        key = kind, mid
        self.pending[key] = future
        try:
            reasons = await self._wait(future, cfg.timeout)
        finally:
            self.pending.pop(key, None)
        output = {"topic": cfg.topic, "qos": cfg.qos, "acknowledged": True}
        if kind == "subscribe":
            self.subscribed = True
            output["granted_qos"] = reasons
        else:
            output.update(bytes_sent=len(payload), retained=cfg.retain)
        return output

    async def close(self):
        self.closing = True
        if getattr(self, "client", None):
            self.client.disconnect()
            await asyncio.to_thread(self.client.loop_stop)
            self.client = None
        for future in getattr(self, "pending", {}).values():
            if not future.done():
                future.cancel()
