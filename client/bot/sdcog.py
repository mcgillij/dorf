import random
import uuid
import json
import logging

import discord
from discord.ext import commands
import websocket
import urllib.request
import urllib.parse
from io import BytesIO

from bot.constants import (
    SPACK_DIR,
)
from bot.utilities import get_random_image_path

logger = logging.getLogger(__name__)


class ImageGen(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.server_address = "127.0.0.1:8188"
        self.client_id = str(uuid.uuid4())

    def queue_prompt(self, prompt):
        p = {"prompt": prompt, "client_id": self.client_id}
        data = json.dumps(p).encode("utf-8")
        req = urllib.request.Request(f"http://{self.server_address}/prompt", data=data)
        return json.loads(urllib.request.urlopen(req).read())

    def get_image(self, filename, subfolder, folder_type):
        data = {"filename": filename, "subfolder": subfolder, "type": folder_type}
        url_values = urllib.parse.urlencode(data)
        with urllib.request.urlopen(
            f"http://{self.server_address}/view?{url_values}"
        ) as response:
            return response.read()

    def get_history(self, prompt_id):
        with urllib.request.urlopen(
            f"http://{self.server_address}/history/{prompt_id}"
        ) as response:
            return json.loads(response.read())

    def get_images(self, prompt):
        ws = websocket.WebSocket()
        ws.connect(f"ws://{self.server_address}/ws?clientId={self.client_id}")
        prompt_id = self.queue_prompt(prompt).get("prompt_id")
        if not prompt_id:
            ws.close()
            raise RuntimeError("No prompt_id returned")

        while True:
            out = ws.recv()
            if isinstance(out, str):
                message = json.loads(out)
                if message["type"] == "executing":
                    data = message["data"]
                    if data["prompt_id"] == prompt_id and data["node"] is None:
                        break  # Done

        history = self.get_history(prompt_id).get(prompt_id, {})
        output_images = {}
        for node_id, node_output in history.get("outputs", {}).items():
            if "images" in node_output:
                images_output = []
                for image in node_output["images"]:
                    img_data = self.get_image(
                        image["filename"], image["subfolder"], image["type"]
                    )
                    images_output.append(img_data)
                output_images[node_id] = images_output

        ws.close()
        return output_images

    @commands.command(name="spack", aliases=["", "gi"])
    async def generate_image(self, ctx):
        """Generates an image from war_waifus.json"""
        # await ctx.trigger_typing()
        try:
            with open("war_waifus.json", "r") as f:
                prompt = json.load(f)

                prompt["3"]["inputs"]["seed"] = generate_random_seed()
                prompt["3"]["inputs"]["steps"] = get_random_steps()
                prompt["3"]["inputs"]["cfg"] = get_random_cfg()
                prompt["3"]["inputs"]["sampler_name"] = get_random_sampler()

            images = self.get_images(prompt)
            for node_id, image_datas in images.items():
                for image_data in image_datas:
                    file = discord.File(BytesIO(image_data), filename="output.png")
                    await ctx.send(file=file)
        except Exception as e:
            await self.spack_old(ctx)

    async def spack_old(self, ctx):
        """Sends a random image from the images directory."""
        image_path = get_random_image_path(SPACK_DIR)
        logger.info(f"Image path: {image_path}")
        if image_path:
            try:
                with open(image_path, "rb") as f:
                    picture = discord.File(f)
                    await ctx.send(file=picture)
            except FileNotFoundError:
                await ctx.send("Image file not found (even though path was generated).")
        else:
            await ctx.send(f"No images found in the '{SPACK_DIR}' directory.")


def generate_random_seed() -> int:
    return random.randint(10**14, 10**15 - 1)


def get_random_sampler() -> str:
    return random.choice(
        ["euler_ancestral", "euler", "euler_cfg_pp", "euler_ancestral_cfg_pp"]
    )


def get_random_steps() -> int:
    return random.randint(16, 40)


def get_random_cfg() -> float:
    return random.uniform(5.0, 7.0)


async def setup(bot):
    await bot.add_cog(ImageGen(bot))
    logger.info("ImageGen cog loaded")
