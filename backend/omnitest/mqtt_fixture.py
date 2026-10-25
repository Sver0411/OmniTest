"""Loopback-only aMQTT broker for repeatable protocol acceptance checks."""

import argparse
import asyncio
import logging
import os
import tempfile
from pathlib import Path


def broker_config(port, password_file=None):
    plugins = (
        {"amqtt.plugins.authentication.FileAuthPlugin": {"password_file": str(password_file)}}
        if password_file
        else {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}}
    )
    return {
        "listeners": {
            "default": {"type": "tcp", "bind": f"127.0.0.1:{port}", "max_connections": 32}
        },
        "plugins": plugins,
    }


async def serve(port, password_file):
    from amqtt.broker import Broker

    broker = Broker(broker_config(port, password_file))
    await broker.start()
    try:
        await asyncio.Event().wait()
    finally:
        await broker.shutdown()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18883)
    parser.add_argument("--username", default="omnitest")
    parser.add_argument("--password-env", help="Optional OMNITEST_SECRET_ environment reference")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    with tempfile.TemporaryDirectory(prefix="omnitest-broker-") as folder:
        password_file = None
        if args.password_env:
            from pwdlib import PasswordHash

            if not args.password_env.startswith("OMNITEST_SECRET_") or not os.getenv(
                args.password_env
            ):
                parser.error("Set the referenced OMNITEST_SECRET_ environment variable")
            if not args.username or any(char in args.username for char in ":\r\n"):
                parser.error("Invalid fixture username")
            password_file = Path(folder) / "passwords.txt"
            password_file.write_text(
                args.username
                + ":"
                + PasswordHash.recommended().hash(os.environ[args.password_env])
                + "\n",
                encoding="utf-8",
            )
        try:
            asyncio.run(serve(args.port, password_file))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
