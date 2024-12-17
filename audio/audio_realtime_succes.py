import websockets
import json
import os
from dotenv import load_dotenv
import wave
import pyaudio
import base64
import logging
import time
import queue
import asyncio

# Configure logging for better debugging and information
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load environment variables
load_dotenv(dotenv_path='/home/fikrat/audio/.env')

endpoint = os.getenv("AZURE_REALTIME_ENDPOINT")
deployment = os.getenv("AZURE_REALTIME_NAME")
key = os.getenv("AZURE_OPENAI_API_KEY")

if not all([endpoint, deployment, key]):
    logging.error("Missing environment variables. Check .env file.")
    exit(1)

endpoint = endpoint.replace("https://", "")
url2 = f"wss://{endpoint}/openai/realtime?deployment={deployment}&api-version=2024-10-01-preview"

# Constants for audio processing
CHUNK_DURATION_MS = 100  # Duration of each chunk in ms
SAMPLE_RATE = 24000       # Sampling rate in Hz
CHANNELS = 1              # Number of audio channels
SAMPLE_WIDTH = 2          # Sample width in bytes (pcm16)
FRAMES_PER_CHUNK = int(SAMPLE_RATE * CHUNK_DURATION_MS / 1000)  # Frames per chunk

mic_queue = queue.Queue()


mic_active = False

# Function to clear the audio buffer
'''
def clear_audio_buffer():
    global audio_buffer
    audio_buffer = bytearray()
    print('🔵 Audio buffer cleared.')
'''
def clear_queue_input():
    while not mic_queue.empty():
        try:
           mic_queue.get_nowait()  # Non-blocking
        except queue.Empty:
            pass         
        logging.info("🪥Queue cleared!")
# Function to stop audio playback
def stop_audio_playback():
    global is_playing
    is_playing = False
    print('🔵 Stopping audio playback.')

async def connect():
    headers = {"api-key": key}
    try:
        async with websockets.connect(url2, additional_headers=headers) as websocket:
            logging.info("Connected to server.")

            # Receive initial session creation message
            initial_message = await websocket.recv()
            session_data = json.loads(initial_message)
            logging.info(f"Session created: {json.dumps(session_data, indent=2)}")

            # Send session update with parameters
            session_update = {
                "type": "session.update",
                "session": {
                    "modalities": ["text", "audio"],
                    "instructions": "You are a helpful assistant.",
                    "voice": "alloy",
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "input_audio_transcription": {
                        "model": "whisper-1"
                    },
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 200
                    },
                    "temperature": 0.8,
                    "max_response_output_tokens": 1000
                }
            }
            
            await websocket.send(json.dumps(session_update))
            logging.info("Session update sent.")

            # Create concurrent tasks for sending and receiving
            send_task = asyncio.create_task(send_audio_file(websocket))
            
            # Create concurrent tasks for recieving 
            receive_task = asyncio.create_task(process_server_response(websocket))

             # Wait for both tasks to complete
            done, pending = await asyncio.wait(
                [send_task, receive_task],
                return_when=asyncio.FIRST_EXCEPTION
            )
            
            # If any task raises an exception, cancel the other
            for task in pending:
                task.cancel()

            logging.info("Done processing. Ready for the next interaction.")

    except websockets.exceptions.ConnectionClosedError as e:
        logging.error(f"WebSocket connection error: {e}")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")


def mic_callback(in_data, frame_count, time_info, status):
    """Microphone callback to capture audio chunks."""
    global mic_active
    
    if not mic_active:
        
        logging.info("Microphone activated.")
        mic_active = True
        
    mic_queue.put(in_data)
    
    return (None, pyaudio.paContinue)


async def send_audio_file(websocket):
    """Send audio chunks from the queue to the server."""
    try:
        logging.info("User is talking...")
        
        # verify if mic active and queue is not empty
        while mic_active or not mic_queue.empty():
            
            if not mic_queue.empty():
                audio_chunk = mic_queue.get()
                base64_audio = base64.b64encode(audio_chunk).decode('utf-8')
         
                await websocket.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": base64_audio
                }))
                await asyncio.sleep(0.1)
               # logging.info(f"Sending audio chunk with size: {len(base64_audio)} bytes.")
               
        # Commit the audio buffer
        # Commit audio buffer to indicate completion
        await websocket.send(json.dumps({"type": "input_audio_buffer.commit"}))
        logging.info("Audio input committed to the server.")

    except Exception as e:
        logging.error(f"Error sending audio data: {e}")

async def process_server_response(websocket):
    p = pyaudio.PyAudio()

    try:
        stream = p.open(format=pyaudio.paInt16,
                        channels=CHANNELS,
                        rate=SAMPLE_RATE,
                        output=True,
                        frames_per_buffer=FRAMES_PER_CHUNK)
        logging.info("Audio playback stream opened from server.")

        while True:

            message = await websocket.recv()
            message_data = json.loads(message)
            event_type = message_data.get("type")

            if event_type == 'response.audio.delta':
                delta_encoded = message_data.get("delta", "")
                if delta_encoded:
                    audio_content = base64.b64decode(delta_encoded)
                    stream.write(audio_content)
                    logging.info(f"Played audio chunk of {len(audio_content)} bytes.")
            
            elif event_type == 'input_audio_buffer.speech_started':
                    print('🔵 AI Speech started.')
                
                    
            elif event_type == 'input_audio_buffer.speech_end':
                    print('🔵 AI Speech end')
                    
            elif event_type == 'response.audio.done':
                    print('🔵 AI finished speaking.')
                    clear_queue_input()  # Clear queue after the server response is complete.

    except Exception as e:
        logging.error(f"Error processing server response: {e}")

    finally:
        if stream.is_active():
            stream.stop_stream()
            stream.close()
            logging.info("Audio playback stream closed.")
        p.terminate()
        logging.info("PyAudio terminated.")

def main():
    p = pyaudio.PyAudio()

    mic_stream = p.open(
        format=pyaudio.paInt16,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        input=True,
        stream_callback=mic_callback,
        frames_per_buffer=FRAMES_PER_CHUNK
    )
    try:
        mic_stream.start_stream()
        asyncio.run(connect())

    except KeyboardInterrupt:
        logging.info("Shutting down gracefully...")

    finally:
        mic_stream.stop_stream()
        mic_stream.close()
        p.terminate()
        logging.info("Resources released. Exiting.")

if __name__ == "__main__":
    main()