#!/usr/bin/env python3
import argparse
import json
import logging
import os
import subprocess
import sys
import time
import importlib

# Configuration
DRAFTS_DIR = 'drafts'
IMAGES_DIR = 'docs/images'
LOGS_DIR = 'logs'
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds


def setup_logging(date_str):
    os.makedirs(LOGS_DIR, exist_ok=True)
    log_file = os.path.join(LOGS_DIR, f"{date_str}.log")
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logging.info(f"Logging initialized. Writing to {log_file}")


def read_draft(date_str):
    draft_path = os.path.join(DRAFTS_DIR, f"{date_str}.json")
    logging.info(f"Reading draft from {draft_path}")

    if not os.path.exists(draft_path):
        error_msg = f"Draft file not found: {draft_path}"
        logging.error(error_msg)
        raise FileNotFoundError(error_msg)

    try:
        with open(draft_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data
    except json.JSONDecodeError as e:
        logging.error(f"Failed to parse JSON: {e}")
        raise
    except Exception as e:
        logging.error(f"Error reading draft file: {e}")
        raise


def construct_prompt(data):
    title = data.get('title', '')
    content = data.get('content', '') or data.get('body', '') or ''

    sentences = [s.strip() for s in content.replace('\n', ' ').split('.') if s.strip()]
    intro_text = '. '.join(sentences[:3])
    if intro_text:
        intro_text += '.'

    prompt = f"{title}. {intro_text}"
    logging.info(f"Constructed prompt: {prompt}")
    return prompt


def generate_image_api(prompt, output_path):
    logging.info("Attempting generation via nanobanana Python API...")
    nanobanana = importlib.import_module("nanobanana")
    nanobanana.generate(
        prompt=prompt,
        width=1024,
        height=1024,
        format='webp',
        output=output_path
    )


def generate_image_cli(prompt, output_path):
    logging.info("Attempting generation via nanobanana CLI...")

    cmd = [
        "nanobanana",
        "generate",
        "--prompt", prompt,
        "--width", "1024",
        "--height", "1024",
        "--format", "webp",
        "--output", output_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"CLI failed with code {result.returncode}: {result.stderr}")

    logging.info(f"CLI Output: {result.stdout}")


def generate_image(prompt, date_str):
    output_path = os.path.join(IMAGES_DIR, f"{date_str}.webp")
    os.makedirs(IMAGES_DIR, exist_ok=True)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logging.info(f"Generation attempt {attempt}/{MAX_RETRIES}")
            try:
                generate_image_api(prompt, output_path)
                logging.info(f"Successfully generated image via API at {output_path}")
                return
            except ImportError:
                logging.warning("nanobanana python module not found. Falling back to CLI.")
            except Exception as e:
                logging.warning(f"Python API failed: {e}. Falling back to CLI.")

            generate_image_cli(prompt, output_path)
            logging.info(f"Successfully generated image via CLI at {output_path}")
            return

        except Exception as e:
            logging.error(f"Attempt {attempt} failed: {e}")
            if attempt < MAX_RETRIES:
                logging.info(f"Retrying in {RETRY_DELAY} seconds...")
                time.sleep(RETRY_DELAY)
            else:
                logging.error("All retry attempts failed.")
                raise


def main():
    parser = argparse.ArgumentParser(description="Generate image from draft using nanobanana.")
    parser.add_argument('--date', required=True, help='Date in YYYY-MM-DD format')
    args = parser.parse_args()

    date_str = args.date

    try:
        setup_logging(date_str)
        logging.info("Starting image generation task")

        draft_data = read_draft(date_str)
        prompt = construct_prompt(draft_data)
        generate_image(prompt, date_str)

        logging.info("Task completed successfully")

    except Exception as e:
        logging.critical(f"Task failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
