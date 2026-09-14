import asyncio
import os

from dotenv import load_dotenv
from tapo import ApiClient


load_dotenv()


async def main():
    username = os.environ["TAPO_USERNAME"]
    password = os.environ["TAPO_PASSWORD"]
    device_ip = os.environ["TAPO_DEVICE_IP"]

    client = ApiClient(username, password)
    plug = await client.p110(device_ip)

    device_info = await plug.get_device_info()
    was_on = device_info.device_on
    print(f"Initial state: {'ON' if was_on else 'OFF'}")

    if was_on:
        print("Turning plug off...")
        await plug.off()
    else:
        print("Turning plug on...")
        await plug.on()

    device_info = await plug.get_device_info()
    print(f"Current state: {'ON' if device_info.device_on else 'OFF'}")


if __name__ == "__main__":
    asyncio.run(main())
