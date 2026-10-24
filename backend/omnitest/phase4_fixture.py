"""Local protobuf service and fault-controlled HTTP service for reproducible acceptance."""

import argparse
import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


def descriptor_bytes():
    from google.protobuf import descriptor_pb2, struct_pb2

    schema = descriptor_pb2.FileDescriptorProto(
        name="echo.proto",
        package="omnitest.fixture",
        syntax="proto3",
        dependency=["google/protobuf/struct.proto"],
    )
    service = schema.service.add(name="EchoService")
    for name in ["Echo", "Reject", "Slow"]:
        service.method.add(
            name=name, input_type=".google.protobuf.Struct", output_type=".google.protobuf.Struct"
        )
    bundle = descriptor_pb2.FileDescriptorSet()
    bundle.file.add().ParseFromString(struct_pb2.DESCRIPTOR.serialized_pb)
    bundle.file.add().CopyFrom(schema)
    return bundle.SerializeToString()


async def grpc_server(port=0):
    import grpc
    from google.protobuf import struct_pb2

    async def echo(request, context):
        return request

    async def reject(request, context):
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Rejected by fixture")

    async def slow(request, context):
        await asyncio.sleep(10)
        return request

    server = grpc.aio.server()
    server.add_generic_rpc_handlers(
        [
            grpc.method_handlers_generic_handler(
                "omnitest.fixture.EchoService",
                {
                    name: grpc.unary_unary_rpc_method_handler(
                        handler,
                        request_deserializer=struct_pb2.Struct.FromString,
                        response_serializer=struct_pb2.Struct.SerializeToString,
                    )
                    for name, handler in [("Echo", echo), ("Reject", reject), ("Slow", slow)]
                },
            )
        ]
    )
    bound = server.add_insecure_port(f"127.0.0.1:{port}")
    await server.start()
    return server, bound


class FaultConfig(BaseModel):
    failures: int = Field(default=0, ge=0, le=100)
    delay_seconds: float = Field(default=0, ge=0, le=5)


def http_app():
    app = FastAPI(title="Local reliability fixture")
    states = {}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/faults/{identity}")
    async def fault(identity: str, config: FaultConfig):
        if len(identity) > 100:
            raise HTTPException(400, "Invalid identity")
        states[identity] = config.model_dump()
        return {"configured": True}

    @app.get("/service/{identity}")
    async def service(identity: str):
        state = states.setdefault(identity, {"failures": 0, "delay_seconds": 0})
        failed = state["failures"] > 0
        if failed:
            state["failures"] -= 1
        await asyncio.sleep(state["delay_seconds"])
        return JSONResponse(
            {"status": "unavailable" if failed else "ok"}, status_code=503 if failed else 200
        )

    return app


async def serve(args):
    import uvicorn

    path = Path(args.descriptor_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(descriptor_bytes())
    server, _ = await grpc_server(args.grpc_port)
    try:
        await uvicorn.Server(
            uvicorn.Config(http_app(), host="127.0.0.1", port=args.http_port)
        ).serve()
    finally:
        await server.stop(0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--http-port", type=int, default=8785)
    parser.add_argument("--grpc-port", type=int, default=8786)
    parser.add_argument("--descriptor-file", default="data/phase4/descriptors/echo.pb")
    asyncio.run(serve(parser.parse_args()))


if __name__ == "__main__":
    main()
