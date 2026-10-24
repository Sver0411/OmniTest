"""Local TCP byte echo fixture with deterministic connection cleanup."""

import argparse
import asyncio


async def echo(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (OSError, asyncio.CancelledError):
        pass
    finally:
        writer.close()
        await writer.wait_closed()


async def main(port):
    server = await asyncio.start_server(echo, "127.0.0.1", port)
    print(f"TCP fixture listening on 127.0.0.1:{port}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    asyncio.run(main(parser.parse_args().port))
