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
#import webrtcvad
#from pydub import AudioSegment

# Load environment variables
load_dotenv(dotenv_path='/stream_custom/server_audio_realtime/.env')


# Configure logging for better debugging and information
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class RealTimeAudio:
    
    
    def __init__(self, CHUNK_DURATION_MS, SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH, FRAMES_PER_CHUNK):
        
    
        self.endpoint = os.getenv("AZURE_REALTIME_ENDPOINT")
        self.deployment = os.getenv("AZURE_REALTIME_NAME")
        self.key = os.getenv("AZURE_OPENAI_API_KEY")
        
        if not all([self.endpoint, self.deployment, self.key]):
            logging.error("Missing environment variables. Check .env file.")
            exit(1)
        
        self.endpoint = self.endpoint.replace("https://", "")
        self.url2 = f"wss://{self.endpoint}/openai/realtime?deployment={self.deployment}&api-version=2024-10-01-preview"

        
        # Constants for audio processing
        self.__CHUNK_DURATION_MS = CHUNK_DURATION_MS  # Duration of each chunk in ms
        self.__SAMPLE_RATE =  SAMPLE_RATE     # Sampling rate in Hz
        self.__CHANNELS = CHANNELS             # Number of audio channels
        self.__SAMPLE_WIDTH =SAMPLE_WIDTH        # Sample width in bytes (pcm16)
        self.__FRAMES_PER_CHUNK = FRAMES_PER_CHUNK
        
        self._mic_queue = queue.Queue()
      
        
      
        self.mic_active = False
        self.awaiting_response_commiting = False
        self.pause_ai_response = False
      
        
        
           
    async def connect(self):
    
        headers = {"api-key": self.key}
        try:
            async with websockets.connect(self.url2, additional_headers=headers) as websocket:
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
                                                "enum": ['celsius',  'fahrenheit']
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
                send_task = asyncio.create_task(self.send_audio_file(websocket))
                    
                # Create concurrent tasks for recieving 
                receive_task = asyncio.create_task(self.process_server_response(websocket))
                    
              
            
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

    # Function to clear the audio buffer
    def _clear_queue_input(self):
        if not self._mic_queue.empty():
            try:
                self._mic_queue.get_nowait()  # Non-blocking
            except queue.Empty:
                pass  
            logging.info("🪥-> Queue cleared!")
            
    def mic_callback(self, in_data, frame_count, time_info, status):
        """Microphone callback to capture audio chunks."""
        
        
        if not self.mic_active:
            logging.info("Microphone activated.")
            self.mic_active = True
  
        self._mic_queue.put(in_data)
        
        return (None, pyaudio.paContinue)
    
    
    async def send_audio_file(self, websocket):
    
        """Send audio chunks from the queue to the server."""
        try:
        
            # Wait until speech actually starts (set by process_server_response)
        
      
            # verify if mic active and queue is not empty,  awaaiting response should be empyty to verify if waitins model response
            while (self.mic_active and not self.awaiting_response_commiting) or not self._mic_queue.empty():
                if not self._mic_queue.empty():
                
                    audio_chunk = self._mic_queue.get()
                    base64_audio = base64.b64encode(audio_chunk).decode('utf-8')
                
                    await websocket.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": base64_audio
                    }))
                    
                
                else:
                    await asyncio.sleep(0.01)
               
                
            # Commit the audio buffer
            # Commit audio buffer to indicate completion
        
            await websocket.send(json.dumps({"type": "input_audio_buffer.commit"}))
            self.awaiting_response_commiting = True
        
        
            logging.info("Audio input committed to the server.")
        
        
        except Exception as e:
            logging.error(f"Error sending audio data: {e}")
            
    
    async def process_server_response(self, websocket):
    
      
        p = pyaudio.PyAudio()    
    
        try:
            stream = p.open(format=pyaudio.paInt16,
                            channels=self.__CHANNELS,
                            rate=self.__SAMPLE_RATE,
                            output=True,
                            frames_per_buffer=self.__FRAMES_PER_CHUNK)
            logging.info("Audio playback stream opened from server.")
            
            while self.mic_active:
            
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
                        "type": "conversation.item.truncate"}))
                        
                
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
                      
                    self._clear_queue_input()
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
                    self._clear_queue_input()
                    
                    #self.awaiting_response_commiting = False
                
        except Exception as e:
            logging.error(f"Error processing server response: {e}")
        finally:
            if stream.is_active():
                stream.stop_stream()
                stream.close()
                logging.info("Audio playback stream closed.")
            p.terminate()
            logging.info("PyAudio terminated.")
            
            
'''
def main():
    
      
    CHUNK_DURATION_MS = 50  # Duration of each chunk in ms
    SAMPLE_RATE = 24000       # Sampling rate in Hz
    CHANNELS = 1              # Number of audio channels
    SAMPLE_WIDTH = 2     
        
    FRAMES_PER_CHUNK = int(SAMPLE_RATE *CHUNK_DURATION_MS / 1000)  # Frames per chunk    
  
    realtimeAudio = RealTimeAudio(CHUNK_DURATION_MS , SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH,FRAMES_PER_CHUNK)
   
    # Setup PyAudio
    p = pyaudio.PyAudio()
    
    mic_stream = p.open(
        format=pyaudio.paInt16,
        channels=CHANNELS, 
        rate=SAMPLE_RATE,
        input=True,
        stream_callback=realtimeAudio.mic_callback,
        frames_per_buffer=FRAMES_PER_CHUNK
    )
    

    print("Listening for speech...")
    try:
        mic_stream.start_stream()
        asyncio.run(realtimeAudio.connect())    
      

    except KeyboardInterrupt:
        print("Stopping...")

    finally:
        mic_stream.stop_stream()
        mic_stream.close()
        p.terminate()
        print("Terminated.")

if __name__ == "__main__":
    main()
    '''