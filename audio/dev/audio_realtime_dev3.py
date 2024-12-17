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
import webrtcvad
from pydub import AudioSegment

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
CHUNK_DURATION_MS = 50  # Duration of each chunk in ms
SAMPLE_RATE = 24000       # Sampling rate in Hz
CHANNELS = 1              # Number of audio channels
SAMPLE_WIDTH = 2          # Sample width in bytes (pcm16)
FRAMES_PER_CHUNK = int(SAMPLE_RATE * CHUNK_DURATION_MS / 1000)  # Frames per chunk

mic_queue = queue.Queue()
vad_queue = queue.Queue()

mic_active = False
awaiting_response_commiting = False
pause_ai_response = False


vad = webrtcvad.Vad()
vad.set_mode(3)# Aggressive mode: 0-3


# Function to clear the audio buffer
def clear_queue_input():
    if not mic_queue.empty():
        try:
            mic_queue.get_nowait()  # Non-blocking
        except queue.Empty:
            pass  
        logging.info("🪥-> Queue cleared!")
       
        
# Function to stop audio playback
def stop_audio_playback():
    global is_playing
    is_playing = False
    print('🔵 Stopping audio playback.')
    
def mic_callback(in_data, frame_count, time_info, status):
    """Microphone callback to capture audio chunks."""
    global mic_active,pause_ai_response
    
    if not mic_active:
        logging.info("Microphone activated.")
        mic_active = True
  
    mic_queue.put(in_data)
    vad_queue.put(in_data)
    return (None, pyaudio.paContinue)
    
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
                    "instructions": """
                    You are a Uninettino AI assistant.You have to help and guide professors and students.
                    Always summarize and answer briefly.
                    """,
                    "voice": "alloy",
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "input_audio_transcription": {
                        "model": "whisper-1"
                    },
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.3,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 150
                    },
                    "tools": [
                            {
                        "type": "function",
                        "name": "get_weather",
                        "description":  "Get the weather at a given location",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "location": { 
                                    "type": "string" ,
                                    "description":"Italy, Rome",
                                    },
                                  "scale": {
                                            "type": "string",
                                            "enum": ['celsius', 'farenheit']
                                    },
                                
                            },
                            
                            "required": ["location", "scale"],
                           
                        }
                    }
                        ],
                    "temperature": 0.4,
                    "max_response_output_tokens": 6000
                }
            }
            
            await websocket.send(json.dumps(session_update))
            logging.info("Session update sent.")

          
            # Create concurrent tasks for sending and receiving
            send_task = asyncio.create_task(send_audio_file(websocket))
                
            # Create concurrent tasks for recieving 
            receive_task = asyncio.create_task(process_server_response(websocket))
                
            #speech_handling = asyncio.create_task(vad_detect())
            vad_task = asyncio.create_task(vad_detect(websocket))
           
             # Wait for both tasks to complete
            done, pending = await asyncio.wait(
                [send_task, receive_task ,vad_task],
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


async def send_audio_file(websocket):
    global awaiting_response_commiting
    """Send audio chunks from the queue to the server."""
    try:
      
        # Wait until speech actually starts (set by process_server_response)
       
        print("Awaiting ", awaiting_response_commiting)
        
        # verify if mic active and queue is not empty,  awaaiting response should be empyty to verify if waitins model response
        while (mic_active and not awaiting_response_commiting) or not mic_queue.empty():
            if not mic_queue.empty():
            
                audio_chunk = mic_queue.get()
                base64_audio = base64.b64encode(audio_chunk).decode('utf-8')
            
                await websocket.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": base64_audio
                }))
                
                #if mic_queue.empty():
                #    await asyncio.sleep(0.01)
            else:
                await asyncio.sleep(0.01)
               # logging.info(f"Sending audio chunk with size: {len(base64_audio)} bytes.")
               
        # Commit the audio buffer
        # Commit audio buffer to indicate completion
       
        await websocket.send(json.dumps({"type": "input_audio_buffer.commit"}))
        awaiting_response_commiting = True
        print("AFTER AWATIINT ", awaiting_response_commiting)
       
        logging.info("Audio input committed to the server.")
        
        # here we set awaitin response true, to let responsew to be arrived succesfuly
       
    except Exception as e:
        logging.error(f"Error sending audio data: {e}")

async def process_server_response(websocket):
    
    global awaiting_response_commiting, mic_active
    p = pyaudio.PyAudio()    
   
    try:
        stream = p.open(format=pyaudio.paInt16,
                        channels=CHANNELS,
                        rate=SAMPLE_RATE,
                        output=True,
                        frames_per_buffer=FRAMES_PER_CHUNK)
        logging.info("Audio playback stream opened from server.")
        
        while mic_active:
           
            message = await websocket.recv()
          
            message_data = json.loads(message)
            event_type = message_data.get("type")
            truncated = False
            
            if event_type == 'input_audio_buffer.speech_started':
                    truncated=True
                    print('🔵 User talking ...')
                    # Immediately cancel if user is already speaking
                    logging.info("User started speaking. Truncating AI response.")
                    
                    #await websocket.send(json.dumps({ "event_id": "event_567",
                    #                                "type": "response.cancel"}))
                    await websocket.send(json.dumps({
                        "event_id": "event_678",
                        "type": "conversation.item.truncate",
                        "item_id": "msg_002",
                        "content_index": 0,
                        "audio_end_ms": 1500
                    }))
                    
            
            elif event_type == 'response.audio.delta':
                logging.info("🔵^^^^^talking AI^^^^^^^^🔵")
                
                if not truncated:
                    
                    # Only play audio if user not speaking
                    delta_encoded = message_data.get("delta", "")
                    
                    if delta_encoded:
                        audio_content = base64.b64decode(delta_encoded)    
                        if len(audio_content) >= 6000:
                            await websocket.send(json.dumps({
                            "type": "conversation.item.truncate",
                            "content_index":6000,
                        }))
                        stream.write(audio_content)
                       
            elif event_type == 'input_audio_buffer.speech_end':
                print('🔵 AI Speech end')
                clear_queue_input()
                """  if awaiting_response_commiting:
                    awaiting_response_commiting=False 
                """
                truncated = False
                    
            elif event_type == 'response.done':
                """
                Returned when the model-generated audio is done.
                Also emitted when a Response is interrupted, incomplete, or cancelled.
                """
                print('🔵 AI Response Done.')
                clear_queue_input()
            
    except Exception as e:
        logging.error(f"Error processing server response: {e}")
    finally:
        if stream.is_active():
            stream.stop_stream()
            stream.close()
            logging.info("Audio playback stream closed.")
        p.terminate()
        logging.info("PyAudio terminated.")
        
# web socket vad detection

async def vad_detect(websocket):
    global pause_ai_response, mic_active
    
    frame_size = 320  # 10 ms frames at 16kHz

    while mic_active:
        if not vad_queue.empty():
            chunk = vad_queue.get()
            
            # Resample
            segment = AudioSegment(
                data=chunk,
                sample_width=SAMPLE_WIDTH,
                frame_rate=SAMPLE_RATE,
                channels=CHANNELS
            )
            segment_16k = segment.set_frame_rate(16000)
            chunk_16k = segment_16k.raw_data

            speaking_detected = False
            for i in range(0, len(chunk_16k), frame_size):
                frame = chunk_16k[i:i + frame_size]
                if len(frame) < frame_size:
                    continue

                if vad.is_speech(frame, 16000):
                    speaking_detected = True
                    break

            if speaking_detected:
                # If user just started speaking now
                if not pause_ai_response:
                    pause_ai_response = True
                    logging.info("User started speaking. Sending response.cancel to stop AI.")
                    #await websocket.send(json.dumps({"type": "response.cancel"}))
            else:
                # No speech detected now
                if pause_ai_response:
                    logging.info("User stopped speaking.")
                    pause_ai_response = False
        else:
            await asyncio.sleep(0.1)

def main():
  
    # Setup PyAudio
    p = pyaudio.PyAudio()
    
    mic_stream = p.open(
        format=pyaudio.paInt16,
        channels=CHANNELS, 
        rate=SAMPLE_RATE,
        input=True,
        stream_callback=mic_callback,
        frames_per_buffer=FRAMES_PER_CHUNK
    )
    
    mic_stream.start_stream()
    print("Listening for speech...")
    try:
        mic_stream.start_stream()
        asyncio.run(connect())    
      

    except KeyboardInterrupt:
        print("Stopping...")

    finally:
        mic_stream.stop_stream()
        mic_stream.close()
        p.terminate()
        print("Terminated.")

if __name__ == "__main__":
    main()