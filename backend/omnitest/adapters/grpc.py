"""Descriptor-driven protobuf unary RPCs against an explicitly scoped endpoint."""

from pydantic import Field

from omnitest.core.ir import Model
from omnitest.core.security import check_url, contained

from .base import AdapterInfo


class Endpoint(Model):
    host: str = Field(pattern=r"^[A-Za-z0-9_.:%-]+$")
    port: int = Field(ge=1, le=65535)
    tls: bool = False
    descriptor_set: str
    allowed_methods: list[str] = Field(min_length=1, max_length=100)
    allow_rpc: bool = False


class Unary(Model):
    method: str = Field(pattern=r"^/[\w.]+/\w+$")
    request: dict = Field(default_factory=dict)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=16)
    timeout: float = Field(default=5, ge=0.05, le=30)


def load_descriptors(path):
    from google.protobuf import descriptor_pb2, descriptor_pool

    if path.stat().st_size > 2_000_000:
        raise ValueError("Descriptor set exceeds 2 MB limit")
    descriptors = descriptor_pb2.FileDescriptorSet.FromString(path.read_bytes())
    pool = descriptor_pool.DescriptorPool()
    remaining = list(descriptors.file)
    while remaining:
        pending = []
        for item in remaining:
            try:
                pool.Add(item)
            except TypeError:
                pending.append(item)
        if len(pending) == len(remaining):
            raise ValueError("Invalid descriptor set or missing imports; include all dependencies")
        remaining = pending
    return pool


class GrpcAdapter:
    info = AdapterInfo(
        "grpc",
        "1.0",
        ["unary", "protobuf", "metadata", "tls"],
        ["omnitest[grpc]", "protobuf descriptor set", "target allow_rpc=true"],
        ["grpc"],
    )

    async def check(self, context):
        try:
            import grpc
        except ImportError as exc:
            raise RuntimeError("Install omnitest[grpc] to enable gRPC execution") from exc
        cfg = Endpoint.model_validate(context.target["config"])
        if not cfg.allow_rpc:
            raise ValueError("gRPC calls require explicit target allow_rpc authorization")
        host = f"[{cfg.host}]" if ":" in cfg.host else cfg.host
        check_url(f"http://{host}:{cfg.port}", context.settings.allowed_hosts)
        self.pool = load_descriptors(
            contained(context.settings.grpc_descriptor_root, cfg.descriptor_set)
        )
        self.allowed_methods = set(cfg.allowed_methods)
        options = [
            ("grpc.max_receive_message_length", 1_000_000),
            ("grpc.max_send_message_length", 1_000_000),
            ("grpc.enable_retries", 0),
            ("grpc.enable_http_proxy", 0),
        ]
        endpoint = f"{host}:{cfg.port}"
        self.channel = (
            grpc.aio.secure_channel(endpoint, grpc.ssl_channel_credentials(), options=options)
            if cfg.tls
            else grpc.aio.insecure_channel(endpoint, options=options)
        )

    async def execute(self, step, context):
        import re

        import grpc
        from google.protobuf import json_format, message_factory

        if step.action != "unary":
            raise ValueError("gRPC adapter supports unary only")
        cfg = Unary.model_validate(step.config)
        if cfg.method not in self.allowed_methods:
            raise ValueError("RPC method is outside the target allowlist")
        if any(
            not re.fullmatch(r"[a-z0-9_.-]+", key) or key.endswith("-bin") for key in cfg.metadata
        ):
            raise ValueError("Metadata requires lowercase text keys without -bin suffix")
        service, method = cfg.method[1:].split("/")
        descriptor = self.pool.FindServiceByName(service).FindMethodByName(method)
        if descriptor.client_streaming or descriptor.server_streaming:
            raise ValueError("Streaming RPCs are not supported by unary execution")
        request_type = message_factory.GetMessageClass(descriptor.input_type)
        response_type = message_factory.GetMessageClass(descriptor.output_type)
        request = json_format.ParseDict(cfg.request, request_type(), descriptor_pool=self.pool)
        wire = request.SerializeToString()
        if len(wire) > 1_000_000:
            raise ValueError("Request exceeds 1 MB limit")
        call = self.channel.unary_unary(
            cfg.method,
            request_serializer=lambda value: value,
            response_deserializer=response_type.FromString,
        )(wire, timeout=cfg.timeout, metadata=list(cfg.metadata.items()))
        try:
            response = await call
            return {
                "status": "OK",
                "response": json_format.MessageToDict(
                    response, preserving_proto_field_name=True, descriptor_pool=self.pool
                ),
            }
        except grpc.aio.AioRpcError as exc:
            return {"status": exc.code().name, "response": None, "details": exc.details()}

    async def close(self):
        if getattr(self, "channel", None):
            await self.channel.close()
            self.channel = None
