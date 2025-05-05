import random
import uuid
import json
import logging
from io import BytesIO
from pathlib import Path
import tempfile
from typing import List, Dict

import asyncio
import aiohttp
import discord
from discord.ext import commands
import websockets

# import websocket
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

WAIFU_PROMPTS = "waifu_prompts.json"
_cached_prompts = ""
QUALITY_PROMPT_SUFFIX = [", masterpiece", "best quality", "amazing quality"]
MAX_IMAGE_HEIGHT = 1024


CHECKPOINTS_FILE = "checkpoints.json"
YARA_PROMPT = "dndchars/yara.json"
ALVYS_PROMPT = "dndchars/alvys.json"
CRUMB_PROMPT = "dndchars/crumb.json"
HALBERD_PROMPT = "dndchars/halberd.json"
IANCAN_PROMPT = "dndchars/iancan.json"

logger = logging.getLogger(__name__)


class ImageGen(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.server_address = "127.0.0.1:8188"
        self.client_id = str(uuid.uuid4())
        self.emoji = "🎨"
        self.photo_emoji = "📷"
        # queue for reaction / image -> image
        self.reaction_queue = asyncio.Queue()  # In-memory queue
        self.processing_task = self.bot.loop.create_task(self.process_queue())
        # spack queue
        self.unified_queue = asyncio.Queue()  # In-memory queue for image generation
        self.image_processing_task = self.bot.loop.create_task(
            self.process_unified_queue()
        )

    async def process_unified_queue(self):
        logger.info("Processing unified queue...")
        while True:
            task_type, data = await self.unified_queue.get()
            logger.info(f"Processing task of type: {task_type}")
            try:
                if task_type == "spack":
                    await self.process_image_request(data)
                elif task_type == "dnd":
                    ctx, character = data
                    await self.process_dnd_image_request(ctx, character)
                elif task_type == "custom":
                    logger.info(f"Processing custom image request")
                    ctx = data["ctx"]
                    image_data = data["image_data"]
                    user_prompt = data["prompt"]
                    logger.info(f"here is the prompt: {user_prompt}")

                    # Process the image
                    image = self.process_image(image_data)
                    file_path, file_name = save_image_to_input_dir(image)
                    # with open("input_spack.json", "r") as f:
                    # prompt_data = json.load(f)
                    # prompt_data["3"]["inputs"]["text"] = user_prompt
                    # prompt_data["2"]["inputs"]["image"] = file_name
                    logger.info(f"Here is ctx.message{ctx.message}")
                    await self.generate_and_send_images(
                        file_name, ctx.message, user_prompt=user_prompt, photo=False
                    )
            except Exception as e:
                logger.error(f"Error processing unified queue task: {e}")
            finally:
                self.unified_queue.task_done()

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        if user.bot:
            return  # Ignore bot reactions

        if str(reaction.emoji) not in [str(self.emoji), str(self.photo_emoji)]:
            return

        photo = reaction.emoji == self.photo_emoji
        message = reaction.message

        if message.attachments:
            attachment_url = message.attachments[0].url
            await self.reaction_queue.put((attachment_url, message, photo))
            logger.info("Reaction added to queue.")

    async def process_queue(self):
        while True:
            attachment_url, message, photo = await self.reaction_queue.get()
            try:
                await self.process_reaction(attachment_url, message, photo)
            except Exception as e:
                logger.error(f"Error processing reaction: {e}")
            finally:
                self.reaction_queue.task_done()

    async def process_reaction(self, attachment_url, message, photo):
        try:
            image_data = await self.fetch_image(attachment_url)
            image = self.process_image(image_data)
            file_path, file_name = save_image_to_input_dir(image)
            await self.generate_and_send_images(file_name, message, photo)
        except Exception as e:
            logger.error(f"Failed to process the image: {e}")
            await message.channel.send("Failed to process the image.")

    async def fetch_image(self, url):
        headers = {"User-Agent": "Mozilla/5.0"}
        file_request = urllib.request.Request(url, headers=headers)

        return await asyncio.to_thread(
            lambda: json.loads(urllib.request.urlopen(file_request).read())
        )

    def process_image(self, image_data):
        image = Image.open(BytesIO(image_data))
        image = resize_image(image, MAX_IMAGE_HEIGHT)
        image = convert_to_png(image)
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    async def generate_and_send_images(
        self, file_name, message, user_prompt=None, photo=False
    ):
        logger.info(
            f"Generating images for {file_name} with user prompt: {user_prompt}"
        )
        try:
            with open("input_spack.json", "r") as f:
                prompt = json.load(f)
                self.update_prompt(prompt, file_name, user_prompt, photo)

            images = await self.get_images(prompt)
            for image_datas in images.values():
                for image_data in image_datas:
                    file = discord.File(BytesIO(image_data), filename="output.png")
                    asyncio.create_task(
                        message.channel.send(file=file)
                    )  # Send asynchronously
                    # await message.channel.send(file=file)
            await message.channel.send("Done processing ...")
        except Exception as e:
            logger.info(f"Error while processing the image: {e}")

    def update_prompt(self, prompt, file_name, user_prompt, photo):
        prompt["2"]["inputs"]["image"] = file_name
        prompt["5"]["inputs"]["seed"] = generate_random_seed()
        prompt["5"]["inputs"]["steps"] = get_random_steps()
        prompt["5"]["inputs"]["cfg"] = get_random_cfg()
        prompt["5"]["inputs"]["sampler_name"] = get_random_sampler()
        if photo:
            prompt["5"]["inputs"]["denoise"] = 0.4000000000000001
            if user_prompt:
                prompt["3"]["inputs"]["text"] = user_prompt + ",".join(
                    QUALITY_PROMPT_SUFFIX
                )

            else:
                prompt["3"]["inputs"]["text"] = (
                    "Hot anime version of the people in the image"
                    + ",".join(QUALITY_PROMPT_SUFFIX)
                )
        else:
            if user_prompt:
                prompt["3"]["inputs"]["text"] = user_prompt + ",".join(
                    QUALITY_PROMPT_SUFFIX
                )
            else:
                prompt["3"]["inputs"]["text"] = get_random_prompt() + ",".join(
                    QUALITY_PROMPT_SUFFIX
                )
        logger.info(f"Updated prompt: {prompt}")

    def queue_prompt(self, prompt):
        p = {"prompt": prompt, "client_id": self.client_id}
        data = json.dumps(p).encode("utf-8")
        req = urllib.request.Request(f"http://{self.server_address}/prompt", data=data)
        return json.loads(urllib.request.urlopen(req).read())

    async def get_image(self, filename, subfolder, folder_type):
        data = {"filename": filename, "subfolder": subfolder, "type": folder_type}
        url_values = urllib.parse.urlencode(data)
        url = f"http://{self.server_address}/view?{url_values}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                return await response.read()

    def get_history(self, prompt_id):
        with urllib.request.urlopen(
            f"http://{self.server_address}/history/{prompt_id}"
        ) as response:
            return json.loads(response.read())

    async def get_images(self, prompt):
        logging.info("Connecting to WebSocket server...")
        async with websockets.connect(
            f"ws://{self.server_address}/ws?clientId={self.client_id}",
            max_size=None,  # Disable client-side message size limit
        ) as ws:
            logging.info("Connected to WebSocket server.")

            prompt_id = self.queue_prompt(prompt).get("prompt_id")
            if not prompt_id:
                logging.error("No prompt_id returned.")
                raise RuntimeError("No prompt_id returned")

            logging.info(f"Prompt ID received: {prompt_id}")

            while True:
                logging.info("Waiting for WebSocket message...")
                try:
                    out = await ws.recv()
                    # logging.info(f"Message received: {out}")
                except websockets.exceptions.ConnectionClosedError as e:
                    logging.error(f"WebSocket connection closed: {e}")
                    raise

                if isinstance(out, str):
                    message = json.loads(out)
                    logging.info(f"WebSocket message type: {message['type']}")
                    if message["type"] == "executing":
                        logging.info(f"Executing node: {message['data']['node']}")
                    elif message["type"] == "executed":
                        logging.info(f"Executed node: {message['data']['node']}")
                        if "output" in message["data"]:
                            logging.debug(
                                f"Output received: {message['data']['output']}"
                            )
                    elif message["type"] == "execution_success":
                        logging.info("Execution success.")
                        break  # Done
                    elif message["type"] == "status":
                        logging.info(f"Status update: {message['data']['status']}")
                    else:
                        logging.warning(f"Unexpected message type: {message['type']}")

            logging.info("Fetching history...")
            history = self.get_history(prompt_id).get(prompt_id, {})
            output_images = {}
            for node_id, node_output in history.get("outputs", {}).items():
                if "images" in node_output:
                    images_output = []
                    for image in node_output["images"]:
                        logging.info(f"Fetching image: {image}")
                        img_data = await self.get_image(
                            image["filename"], image["subfolder"], image["type"]
                        )
                        images_output.append(img_data)
                    output_images[node_id] = images_output

            logging.info("Returning output images.")
            return output_images

    @commands.command(name="spack", aliases=["", "gi"])
    async def generate_image(self, ctx):
        """Generates an image from war_waifus.json"""
        if ctx.guild is None:
            await ctx.author.send(
                "Your request has been added to the queue. Please wait..."
            )
        else:
            await ctx.send("Your request has been added to the queue. Please wait...")
        await self.unified_queue.put(("spack", ctx))

    @commands.command(name="dnd", aliases=["d"])
    async def generate_dnd_image(self, ctx, character: str):
        """Generates an image for dnd characters"""
        if character.lower() not in ["alvys", "iancan", "crumb", "halberd", "yara"]:
            await ctx.send(
                "Character must be one of: alvys, iancan, crumb, halberd, yara"
            )
            return

        if ctx.guild is None:
            await ctx.author.send(
                "Your request has been added to the queue. Please wait..."
            )
        else:
            await ctx.send("Your request has been added to the queue. Please wait...")

        await self.unified_queue.put(("dnd", (ctx, character)))

    async def process_dnd_image_request(self, ctx, character):
        logger.info(
            f"Starting process_dnd_image_request for {ctx.author} and character {character}"
        )
        # Existing code
        async with ctx.typing():
            logger.info(f"Processing DnD image request for character: {character}")
            character_prompt = None
            match character.lower():
                case "alvys":
                    character_prompt = ALVYS_PROMPT
                case "iancan":
                    character_prompt = IANCAN_PROMPT
                case "crumb":
                    character_prompt = CRUMB_PROMPT
                case "halberd":
                    character_prompt = HALBERD_PROMPT
                case "yara":
                    character_prompt = YARA_PROMPT
            logger.info(f"Character prompt file: {character_prompt}")

            try:
                dnd_char_prompt = get_character_prompt(character_prompt)
                logger.info(f"Character prompt content: {dnd_char_prompt}")

                with open("war_waifus.json", "r") as f:
                    prompt = json.load(f)

                    checkpoint = get_random_checkpoints()
                    ckpt_name, ckpt_data = next(iter(checkpoint.items()))
                    logger.info(f"Selected checkpoint: {ckpt_name}")

                    prompt["4"]["inputs"]["ckpt_name"] = ckpt_name
                    prompt["3"]["inputs"]["seed"] = generate_random_seed()
                    prompt["3"]["inputs"]["steps"] = get_random_steps(
                        min(ckpt_data["steps"]), max(ckpt_data["steps"])
                    )
                    prompt["3"]["inputs"]["cfg"] = get_random_cfg(
                        min(ckpt_data["cfg"]), max(ckpt_data["cfg"])
                    )
                    prompt["3"]["inputs"]["sampler_name"] = get_random_sampler(
                        ckpt_data["samplers"]
                    )
                    prompt["6"]["inputs"]["text"] = dnd_char_prompt + ",".join(
                        QUALITY_PROMPT_SUFFIX
                    )
                    logger.info(f"Final prompt: {prompt}")

                images = await self.get_images(prompt)
                logger.info(f"Images received: {len(images)} nodes")

                for node_id, image_datas in images.items():
                    logger.info(
                        f"Processing node {node_id} with {len(image_datas)} images"
                    )
                    for image_data in image_datas:
                        file = discord.File(BytesIO(image_data), filename="output.png")
                        asyncio.create_task(ctx.send(file=file))  # Send asynchronously
                        logger.info(f"Image sent to Discord for node {node_id}")
            except Exception as e:
                logger.error(f"Error generating image: {e}")
        logger.info(
            f"Finished process_dnd_image_request for {ctx.author} and character {character}"
        )

    @commands.command(name="custom", aliases=["c"])
    async def generate_custom_image(self, ctx, *, prompt: str):
        """Generates an image based on a user-provided prompt and image."""
        logger.info(f"Received custom image request from {ctx.author}: {prompt}")
        if not ctx.message.attachments or not ctx.message:
            await ctx.send("Please attach an image to your message, and prompt")
            return

        attachment = ctx.message.attachments[0]
        if not attachment.filename.lower().endswith(("png", "jpg", "jpeg")):
            await ctx.send("Please attach a valid image file (PNG, JPG, or JPEG).")
            return

        await ctx.send("Your request has been added to the queue. Please wait...")

        try:
            # Fetch the image
            image_data = await attachment.read()
            logger.info(f"Fetched image data from {attachment.filename}")
            logger.info("Adding to the queue for processing")
            # Add the task to the unified queue

            self.unified_queue.put_nowait(
                ("custom", {"ctx": ctx, "image_data": image_data, "prompt": prompt})
            )
        except Exception as e:
            logger.error(f"Error adding custom image task to queue: {e}")
            await ctx.send("Failed to add your request to the queue.")

    async def process_image_request(self, ctx):
        logger.info(f"Starting process_image_request for {ctx.author}")
        # Existing code
        async with ctx.typing():
            try:
                with open("war_waifus.json", "r") as f:
                    prompt = json.load(f)
                    checkpoint = get_random_checkpoints()
                    ckpt_name, ckpt_data = next(iter(checkpoint.items()))
                    prompt["4"]["inputs"]["ckpt_name"] = ckpt_name
                    prompt["3"]["inputs"]["seed"] = generate_random_seed()
                    prompt["3"]["inputs"]["steps"] = get_random_steps(
                        min(ckpt_data["steps"]), max(ckpt_data["steps"])
                    )
                    prompt["3"]["inputs"]["cfg"] = get_random_cfg(
                        min(ckpt_data["cfg"]), max(ckpt_data["cfg"])
                    )
                    prompt["3"]["inputs"]["sampler_name"] = get_random_sampler(
                        ckpt_data["samplers"]
                    )
                    prompt["6"]["inputs"]["text"] = get_random_prompt() + ",".join(
                        QUALITY_PROMPT_SUFFIX
                    )

                images = await self.get_images(prompt)
                for image_datas in images.values():
                    for image_data in image_datas:
                        file = discord.File(BytesIO(image_data), filename="output.png")
                        asyncio.create_task(ctx.send(file=file))  # Send asynchronously
                        # await ctx.send(file=file)
            except Exception as e:
                logger.error(f"Error generating image: {e}")
                await self.spack_old(ctx)
        logger.info(f"Finished process_image_request for {ctx.author}")

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


def save_image_to_input_dir(image_data):
    with tempfile.NamedTemporaryFile(
        dir=INPUT_IMAGE_DIR, suffix=".png", delete=False
    ) as tmp_file:
        tmp_file.write(image_data)
        file_path = Path(tmp_file.name)
    return (file_path, tmp_file.name)


def resize_image(image, max_size):
    """Resize the image proportionally to fit within max_size."""
    width, height = image.size

    # Ensure the image is scaled up if either dimension is smaller than max_size
    if width < max_size and height < max_size:
        if width > height:
            new_width = max_size
            new_height = int((height / width) * max_size)
        else:
            new_height = max_size
            new_width = int((width / height) * max_size)
    else:
        if width > height:
            new_width = max_size
            new_height = int((height / width) * max_size)
        else:
            new_height = max_size
            new_width = int((width / height) * max_size)

    return image.resize((new_width, new_height))


def convert_to_png(image):
    """Convert the image to PNG format."""
    if image.format != "PNG":
        output = BytesIO()
        image.save(output, format="PNG")
        output.seek(0)
        return Image.open(output)
    return image


def generate_random_seed() -> int:
    return random.randint(10**14, 10**15 - 1)


def get_random_sampler(
    samplers: List = [
        "euler_ancestral",
        "euler",
        "euler_cfg_pp",
        "euler_ancestral_cfg_pp",
    ]
) -> str:
    return random.choice(samplers)


def get_random_steps(min: int = 16, max: int = 35) -> int:
    return random.randint(min, max)


def get_random_cfg(min: float = 5.0, max: float = 7.0) -> float:
    return random.uniform(min, max)


def load_prompts() -> None:
    global _cached_prompts
    with open(WAIFU_PROMPTS, "r") as file:
        _cached_prompts = json.load(file)


def get_character_prompt(prompt_file) -> str:
    with open(prompt_file, "r") as file:
        return random.choice(json.load(file))


def get_random_checkpoints() -> Dict:
    with open(CHECKPOINTS_FILE, "r") as file:
        return random.choice(json.load(file))


def get_random_prompt() -> str:
    if not _cached_prompts:
        load_prompts()
    return random.choice(_cached_prompts)


async def setup(bot):
    await bot.add_cog(ImageGen(bot))
    logger.info("ImageGen cog loaded")
