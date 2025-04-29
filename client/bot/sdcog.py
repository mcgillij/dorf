import random
import uuid
import json
import logging
from io import BytesIO
from pathlib import Path
import tempfile

import asyncio

import discord
from discord.ext import commands
import websocket
import urllib.request
import urllib.parse

from PIL import Image
from io import BytesIO

from bot.constants import (
    SPACK_DIR,
)
from bot.utilities import get_random_image_path

# dir for input images

INPUT_IMAGE_DIR = Path("/home/j/ComfyUI/input")
INPUT_IMAGE_DIR.mkdir(exist_ok=True)

MAX_IMAGE_HEIGHT = 1024


def save_image_to_input_dir(image_data):
    with tempfile.NamedTemporaryFile(
        dir=INPUT_IMAGE_DIR, suffix=".png", delete=False
    ) as tmp_file:
        tmp_file.write(image_data)
        file_path = Path(tmp_file.name)
    return (file_path, tmp_file.name)


logger = logging.getLogger(__name__)


class ImageGen(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.server_address = "127.0.0.1:8188"
        self.client_id = str(uuid.uuid4())
        self.emoji = "🎨"
        self.photo_emoji = "📷"

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        logger.info(f"str emoji {str(reaction.emoji)}")
        logger.info(f"Reaction added by {user}: {reaction.emoji}")
        if user.bot:
            return  # Ignore bot reactions
        logger.info(f"Reaction: {reaction.message}")
        if str(reaction.emoji) not in [str(self.emoji), str(self.photo_emoji)]:
            logger.info(f"Reaction: {reaction.emoji} is not a {self.emoji}")
            return

        photo = False
        if reaction.emoji == self.photo_emoji:
            photo = True

        message = reaction.message
        if message.attachments:
            logger.info(f"Attachment found in message: {message.attachments[0].url}")
            attachment_url = message.attachments[0].url

            try:
                # Fetch the image
                headers = {"User-Agent": "Mozilla/5.0"}
                file_request = urllib.request.Request(attachment_url, headers=headers)

                # Fetch the image
                with urllib.request.urlopen(file_request) as response:
                    image_data = response.read()

                image = Image.open(BytesIO(image_data))
                width, height = image.size
                logger.info(f"Image dimensions: {width}x{height}")

                if width > MAX_IMAGE_HEIGHT or height > MAX_IMAGE_HEIGHT:
                    await message.channel.send(
                        f"Image is too large. Maximum allowed dimensions are {MAX_IMAGE_HEIGHT}x{MAX_IMAGE_HEIGHT}."
                    )
                    return

                file_path, file_name = save_image_to_input_dir(image_data)
                logger.info(f"Image saved to {file_path}")
                await message.channel.send(f"Image processing ...")
                async with reaction.message.channel.typing():
                    try:
                        with open("input_spack.json", "r") as f:
                            prompt = json.load(f)
                            prompt["2"]["inputs"]["image"] = file_name
                            prompt["5"]["inputs"]["seed"] = generate_random_seed()
                            prompt["5"]["inputs"]["steps"] = get_random_steps()
                            if photo:
                                prompt["5"]["inputs"]["denoise"] = 0.4000000000000001
                            prompt["5"]["inputs"]["cfg"] = get_random_cfg()
                            prompt["5"]["inputs"]["sampler_name"] = get_random_sampler()
                            if photo:
                                prompt["3"]["inputs"][
                                    "text"
                                ] = "Hot anime version of the people in the image already, masterpiece, best quality, amazing quality"
                            else:
                                prompt["3"]["inputs"]["text"] = get_random_prompt()

                        images = await asyncio.to_thread(self.get_images, prompt)
                        for node_id, image_datas in images.items():
                            for image_data in image_datas:
                                file = discord.File(
                                    BytesIO(image_data), filename="output.png"
                                )
                                await reaction.message.channel.send(file=file)
                                await message.channel.send(f"Done processing ...")
                    except Exception as e:
                        logger.info(f"Error while processing the image: {e}")
            except Exception as e:
                logger.error(f"Failed to process the image: {e}")
                await message.channel.send("Failed to process the image.")

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
        async with ctx.typing():
            try:
                with open("war_waifus.json", "r") as f:
                    prompt = json.load(f)

                    prompt["3"]["inputs"]["seed"] = generate_random_seed()
                    prompt["3"]["inputs"]["steps"] = get_random_steps()
                    prompt["3"]["inputs"]["cfg"] = get_random_cfg()
                    prompt["3"]["inputs"]["sampler_name"] = get_random_sampler()
                    prompt["6"]["inputs"]["text"] = get_random_prompt()

                images = await asyncio.to_thread(self.get_images, prompt)
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
    return random.randint(16, 35)


def get_random_cfg() -> float:
    return random.uniform(5.0, 7.0)


def get_random_prompt() -> str:
    prompts = [
        "Hot Waifu's in warhammer 40k power armor, large breasts in bikini top,masterpiece,best quality,amazing quality",
        "Hot warhammer 40k succubus, large breasts in bikini top,masterpiece,best quality,amazing quality",
        "Hot warhammer 40k Daughters of the Emperor, large breasts in bikini top,masterpiece,best quality,amazing quality",
        "Hot warhammer 40k Adepta Sororitas, large breasts in bikini top,masterpiece,best quality,amazing quality",
        "Hot warhammer 40k Sisters of Battle, large breasts in bikini top,masterpiece,best quality,amazing quality",
        "Hot warhammer 40k Sisters of Silence, large breasts in bikini top,masterpiece,best quality,amazing quality",
        "Hot warhammer 40k Silent Sisterhood, large breasts in bikini top,masterpiece,best quality,amazing quality",
        "Hot warhammer 40k Null Maidens, large breasts in bikini top,masterpiece,best quality,amazing quality",
        "Hot warhammer 40k Daughtes of the Abyss, large breasts in bikini top,masterpiece,best quality,amazing quality",
    ]
    return random.choice(prompts)


async def setup(bot):
    await bot.add_cog(ImageGen(bot))
    logger.info("ImageGen cog loaded")
