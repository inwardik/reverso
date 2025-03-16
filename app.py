import os
import re
import socket
import sys
import threading
from datetime import timedelta

import pysrt
from termcolor import colored

SEARCH_LIMIT = 12


def to_timedelta(srt_time):
    return timedelta(
        hours=srt_time.hours,
        minutes=srt_time.minutes,
        seconds=srt_time.seconds,
        milliseconds=srt_time.milliseconds,
    )


def is_russian(text):
    return bool(re.search(r"[^\x00-\x7F]", text))  # Any non-ASCII character


def find_closest_sub(start_time, subs, tolerance=timedelta(seconds=1)):
    closest_sub = None
    min_time_diff = timedelta.max

    start_time_td = to_timedelta(start_time)

    for sub in subs:
        sub_start_time_td = to_timedelta(
            sub.start
        )
        time_diff = abs(sub_start_time_td - start_time_td)

        if time_diff <= tolerance and time_diff < min_time_diff:
            min_time_diff = time_diff
            closest_sub = sub

    return closest_sub


def find_translation_in_other_lang(
    directory,
    start_time,
    file_path,
    from_lang_suffix,
    to_lang_suffix,
    tolerance=timedelta(seconds=1),
):
    directory_path = os.path.dirname(file_path)
    file_name = os.path.basename(file_path)

    other_lang_file_name = file_name.replace(
        f"_{from_lang_suffix}", f"_{to_lang_suffix}"
    )
    other_lang_file_path = os.path.join(directory_path, other_lang_file_name)

    if os.path.exists(other_lang_file_path):
        subs = pysrt.open(other_lang_file_path)

        closest_sub = find_closest_sub(start_time, subs, tolerance)
        if closest_sub:
            return (
                closest_sub.text.strip(),
                closest_sub.start,
                closest_sub.end,
                other_lang_file_path,
            )
        else:
            return None, None, None, None
    else:
        return None, None, None, None


def format_single_result(result):
    formatted = []
    orig = result["original"]
    formatted.append(
        f"{orig['language']} ({orig['file']}) {orig['start_time']} -> {orig['end_time']}"
    )
    formatted.append(f"{orig['text']}\n")

    if result["translation"]:
        trans = result["translation"]
        formatted.append(
            f"{trans['language']} ({trans['file']}) {trans['start_time']} -> {trans['end_time']}"
        )
        formatted.append(f"{trans['text']}\n")
    else:
        formatted.append(f"No corresponding translation found.\n")

    formatted.append("-" * 80)
    return "\n".join(formatted)


def stream_subtitle_search(directory, search_string, conn):
    if is_russian(search_string):
        lang_suffix = "ru"
        other_lang_suffix = "en"
    else:
        lang_suffix = "en"
        other_lang_suffix = "ru"

    found_matches = False
    count = 0

    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith(f"_{lang_suffix}.srt"):
                file_path = os.path.join(root, file)
                try:
                    subs = pysrt.open(file_path)
                    for sub in subs:
                        if (
                            search_string.lower() in sub.text.lower()
                        ):
                            found_matches = True
                            count += 1

                            text = (
                                re.sub(r"[ \n]+", " ", sub.text.strip())
                                .replace("<i>", "")
                                .replace("</i>", "")
                            )

                            result = {
                                "original": {
                                    "language": lang_suffix.upper(),
                                    "file": file_path,
                                    "start_time": str(sub.start),
                                    "end_time": str(sub.end),
                                    "text": text,
                                }
                            }

                            (
                                translated_text,
                                translated_start_time,
                                translated_end_time,
                                translated_file_path,
                            ) = find_translation_in_other_lang(
                                directory,
                                sub.start,
                                file_path,
                                lang_suffix,
                                other_lang_suffix,
                            )

                            if translated_text:
                                translated_text = (
                                    re.sub(r"[ \n]+", " ", translated_text)
                                    .strip()
                                    .replace("<i>", "")
                                    .replace("</i>", "")
                                )
                                result["translation"] = {
                                    "language": other_lang_suffix.upper(),
                                    "file": translated_file_path,
                                    "start_time": str(translated_start_time),
                                    "end_time": str(translated_end_time),
                                    "text": translated_text,
                                }
                            else:
                                result["translation"] = None

                            formatted_result = format_single_result(result)
                            conn.sendall(formatted_result.encode())

                            if count >= SEARCH_LIMIT:
                                return True
                except Exception as e:
                    print(f"Error processing file {file_path}: {e}")

    return found_matches


def handle_client(conn, addr, subtitles_directory):
    print(f"[NEW CONNECTION] {addr} connected.")
    try:
        while True:
            search_str = conn.recv(1024).decode().strip()
            if not search_str:
                break

            print(f"[SEARCH] Client {addr} searching for: '{search_str}'")

            found_matches = stream_subtitle_search(
                subtitles_directory, search_str, conn
            )

            if not found_matches:
                no_results_msg = "No matching subtitles found."
                conn.sendall(no_results_msg.encode())

            conn.sendall(b"\n<END>\n")

    except Exception as e:
        print(f"[ERROR] {e}")
    finally:
        conn.close()
        print(f"[DISCONNECTED] {addr} disconnected.")


def start_server(host, port, subtitles_directory):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(5)
    print(f"[LISTENING] Subtitle Search Server is listening on {host}:{port}")
    print(f"[DIRECTORY] Searching in: {subtitles_directory}")

    try:
        while True:
            conn, addr = server.accept()
            thread = threading.Thread(
                target=handle_client, args=(conn, addr, subtitles_directory)
            )
            thread.start()
    except KeyboardInterrupt:
        print("\n[SHUTTING DOWN] Server is shutting down...")
    finally:
        server.close()


def start_client(host, port):
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.connect((host, port))
        print(f"Connected to subtitle search server at {host}:{port}")
        print("Type search queries or 'exit' to quit.")

        while True:
            search_str = input("Search: ")
            if search_str.lower() == "exit":
                break

            client.sendall(search_str.encode())

            receiving = True
            buffer = ""

            while receiving:
                data = client.recv(1024).decode()
                if "<END>" in data:
                    buffer += data.replace("<END>", "")
                    receiving = False
                else:
                    buffer += data
                    lines = buffer.split("\n")
                    if len(lines) > 1:
                        for line in lines[:-1]:
                            if line:
                                print(colored(line, "cyan"))
                        buffer = lines[-1]

            if buffer.strip():
                print(colored(buffer, "cyan"))

    except ConnectionRefusedError:
        print("Could not connect to the server. Make sure it's running.")
    except KeyboardInterrupt:
        print("\nSearch cancelled.")
    finally:
        client.close()
        print("Disconnected from server.")


if __name__ == "__main__":
    HOST = "127.0.0.1"
    PORT = 65432
    SUBTITLES_DIR = "."

    if len(sys.argv) > 1:
        if sys.argv[1] == "server":
            if len(sys.argv) > 2:
                SUBTITLES_DIR = sys.argv[2]
            start_server(HOST, PORT, SUBTITLES_DIR)
        elif sys.argv[1] == "client":
            start_client(HOST, PORT)
        else:
            print("Usage: python script.py [server [directory] | client]")
    else:
        print("Starting in server mode with current directory...")
        start_server(HOST, PORT, SUBTITLES_DIR)
