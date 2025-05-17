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
QUALITY_PROMPT_SUFFIX = [", embedding:lazypos, lazypos"]
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
        self.goblin_emoji = "👺"
        self.retry_emoji = "🔄"
        self.unified_queue = asyncio.Queue()  # In-memory queue for image generation
        self.image_processing_task = self.bot.loop.create_task(
            self.process_unified_queue()
        )

    async def process_unified_queue(self):
        logger.info("Processing unified queue...")
        statemanager = self.bot.get_cog("StateManager")
        while True:
            task_type, data = await self.unified_queue.get()
            logger.info(f"Processing task of type: {task_type}, {data}")
            try:
                if statemanager:
                    statemanager.update_state_thinking()
                if task_type == "spack":
                    ctx = data["ctx"]
                    await self.process_image_request(ctx)
                elif task_type == "spork":
                    ctx = data["ctx"]
                    user_prompt = data["prompt"]
                    await self.process_image_request(ctx, user_prompt)
                elif task_type == "dnd":
                    ctx = data["ctx"]
                    character = data["character"]
                    await self.process_dnd_image_request(ctx, character)
                elif task_type == "draw":
                    logger.info(f"Processing draw image request")
                    ctx = data["ctx"]
                    image_data = data["image_data"]
                    user_prompt = data["prompt"]
                    logger.info(f"here is the prompt: {user_prompt}")

                    # Process the image
                    image = process_image_data(image_data)
                    file_path, file_name = save_image_to_input_dir(image)
                    logger.info(f"Here is ctx.message{ctx.message}")
                    await self.generate_and_send_images(
                        file_name, ctx.message, user_prompt=user_prompt, photo=False
                    )
                elif task_type == "photo":
                    logger.info(f"Processing photo image request")
                    ctx = data["ctx"]
                    image_data = data["image_data"]
                    user_prompt = data["prompt"]
                    logger.info(f"here is the prompt: {user_prompt}")

                    # Process the image
                    image = process_image_data(image_data)
                    file_path, file_name = save_image_to_input_dir(image)
                    logger.info(f"Here is ctx.message{ctx.message}")
                    await self.generate_and_send_images(
                        file_name, ctx.message, user_prompt=user_prompt, photo=True
                    )
                elif task_type == "reaction":
                    logger.info(f"here is my {data}")
                    attachment_url = data["attachment_url"]
                    message = data["message"]
                    photo = data["photo"]
                    await self.process_reaction(attachment_url, message, photo)
                    # "goblin",
                    # {
                    # "attachment_url": attachment_url,
                    # "message": message,
                    # "photo": True,
                    # },
                elif task_type == "goblin":
                    logger.info(f"here is my {data}")
                    attachment_url = data["attachment_url"]
                    message = data["message"]
                    photo = data["photo"]
                    await self.process_reaction(
                        attachment_url, message, photo, goblin=True
                    )

            except Exception as e:
                logger.error(f"Error processing unified queue task: {e}")
            finally:
                if statemanager:
                    statemanager.update_state_idle()
                self.unified_queue.task_done()

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        if user.bot:
            return  # Ignore bot reactions

        if str(reaction.emoji) not in [
            str(self.emoji),
            str(self.photo_emoji),
            str(self.retry_emoji),
            str(self.goblin_emoji),
        ]:
            return

        message = reaction.message

        if str(reaction.emoji) == str(self.retry_emoji):
            # Check if the message contains a previously run command
            if (
                message.content.startswith("!photo")
                or message.content.startswith("!draw")
                or message.content.startswith("!spork")
            ):
                logger.info(f"Retrying command for message: {message.content}")
                # Extract the command and parameters
                command, *parameters = message.content.split(maxsplit=1)
                parameter = parameters[0] if parameters else ""

                # Re-trigger the appropriate command
                ctx = await self.bot.get_context(message)
                if command == "!photo":
                    await self.generate_photo_image(ctx, prompt=parameter)
                elif command == "!draw":
                    await self.generate_draw_image(ctx, prompt=parameter)
                elif command == "!spork":
                    await self.generate_image_request(ctx, parameter=parameter)
            return

        if str(reaction.emoji) in [
            str(self.emoji),
            str(self.photo_emoji),
            str(self.goblin_emoji),
        ]:
            photo = reaction.emoji == self.photo_emoji
            if message.attachments:
                attachment_url = message.attachments[0].url
                if reaction.emoji == str(self.goblin_emoji):
                    await self.unified_queue.put(
                        (
                            "goblin",
                            {
                                "attachment_url": attachment_url,
                                "message": message,
                                "photo": photo,
                            },
                        )
                    )
                    logger.info("Goblin request gone through")
                else:
                    await self.unified_queue.put(
                        (
                            "reaction",
                            {
                                "attachment_url": attachment_url,
                                "message": message,
                                "photo": photo,
                            },
                        )
                    )
                    logger.info("Reaction added to unified queue.")

    async def process_reaction(
        self,
        attachment_url: str,
        message: discord.Message,
        photo: bool,
        goblin: bool = False,
    ):
        try:
            # Download the image from the attachment URL
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment_url) as response:
                    if response.status == 200:
                        image_data = await response.read()  # Read as binary data
                        logger.info("Image successfully downloaded.")

                        # Process the image
                        image = process_image_data(image_data)
                        file_path, file_name = save_image_to_input_dir(image)
                        if goblin:
                            await self.generate_and_send_images(
                                file_name,
                                message,
                                user_prompt="Make the people in the images look like Orcs, green skin orc teeth, angry scowl",
                                photo=photo,
                            )
                        else:
                            await self.generate_and_send_images(
                                file_name, message, user_prompt=None, photo=photo
                            )
                    else:
                        logger.error(
                            f"Failed to download image: HTTP {response.status}"
                        )
        except Exception as e:
            logger.error(f"Failed to process the image: {e}")

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
            await message.channel.send("Done processing ...")
        except Exception as e:
            logger.info(f"Error while processing the image: {e}")

    def update_prompt(self, prompt, file_name, user_prompt, photo):
        prompt["2"]["inputs"]["image"] = file_name
        prompt["5"]["inputs"].update(
            {
                "seed": generate_random_seed(),
                "steps": get_random_steps(),
                "cfg": get_random_cfg(),
                "sampler_name": get_random_sampler(),
            }
        )

        base_text = (
            "Hot anime version of the people in the image"
            if photo
            else get_random_prompt()
        )
        prompt["3"]["inputs"]["text"] = (
            f"{user_prompt}," if user_prompt else base_text
        ) + ",".join(QUALITY_PROMPT_SUFFIX)

        if photo:
            prompt["5"]["inputs"]["denoise"] = 0.4000000000000001

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

    @commands.command(name="generate", aliases=["spack", "dnd", "spork"])
    async def generate_image_request(self, ctx, *, parameter: str = ""):
        """Generates Image based on, !spack, !dnd <character>:str, !spork <prompt>:str"""
        invoked_alias = ctx.invoked_with  # Get the alias or command name used
        if invoked_alias == "dnd":
            if parameter == "" or parameter.lower() not in [
                "alvys",
                "iancan",
                "crumb",
                "halberd",
                "yara",
            ]:
                await ctx.send(
                    "Character must be one of: alvys, iancan, crumb, halberd, yara"
                )
                return
            await ctx.send("Your request has been added to the queue. Please wait...")
            await self.unified_queue.put(("dnd", {"ctx": ctx, "character": parameter}))
        elif invoked_alias == "spork":
            if ctx.guild is not None:
                await ctx.send("This command can only be used in DMs.")
                return
            if parameter == "":
                await ctx.send("You must provide a prompt for the spork command.")
                return
            await ctx.send("Your request has been added to the queue. Please wait...")
            await self.unified_queue.put(("spork", {"ctx": ctx, "prompt": parameter}))
        else:  # Default to spack
            await ctx.send("Your request has been added to the queue. Please wait...")
            await self.unified_queue.put(("spack", {"ctx": ctx}))

    async def process_dnd_image_request(self, ctx, character):
        logger.info(
            f"Starting process_dnd_image_request for {ctx.author} and character {character}"
        )
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

    @commands.command(name="photo", aliases=["ph"])
    async def generate_photo_image(self, ctx, *, prompt: str):
        """Uses an attached image to generate a photo based on a user-provided prompt. format: !photo <prompt>:str (attached image)"""
        if ctx.guild is not None:
            await ctx.send("This command can only be used in DMs.")
            return
        logger.info(f"Received photo image request from {ctx.author}: {prompt}")
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
                ("photo", {"ctx": ctx, "image_data": image_data, "prompt": prompt})
            )
        except Exception as e:
            logger.error(f"Error adding draw image task to queue: {e}")
            await ctx.send("Failed to add your request to the queue.")

    @commands.command(name="draw", aliases=["dr"])
    async def generate_draw_image(self, ctx, *, prompt: str):
        """Uses an attached image to generate a drawing based on a user-provided prompt. format: !draw <prompt>:str (attached image)"""
        logger.info(f"Received draw image request from {ctx.author}: {prompt}")
        if ctx.guild is not None:
            await ctx.send("This command can only be used in DMs.")
            return
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
                ("draw", {"ctx": ctx, "image_data": image_data, "prompt": prompt})
            )
        except Exception as e:
            logger.error(f"Error adding draw image task to queue: {e}")
            await ctx.send("Failed to add your request to the queue.")

    async def process_image_request(self, ctx, user_prompt=None):
        logger.info(f"Starting process_image_request for {ctx.author}")
        logger.info(f"passed in custom prompt: {user_prompt}")
        async with ctx.typing():  # have derf look like he's typing
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
                    if user_prompt:
                        prompt["6"]["inputs"]["text"] = user_prompt + ",".join(
                            QUALITY_PROMPT_SUFFIX
                        )
                    else:
                        prompt["6"]["inputs"]["text"] = get_random_prompt() + ",".join(
                            QUALITY_PROMPT_SUFFIX
                        )

                images = await self.get_images(prompt)
                for image_datas in images.values():
                    for image_data in image_datas:
                        file = discord.File(BytesIO(image_data), filename="output.png")
                        asyncio.create_task(ctx.send(file=file))  # Send asynchronously
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


def process_image_data(image_data):
    """Rescale and convert image data to PNG format."""
    image = Image.open(BytesIO(image_data))
    image = resize_image(image, MAX_IMAGE_HEIGHT)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


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
