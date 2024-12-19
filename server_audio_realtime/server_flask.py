from flask  import Flask, request, jsonify

from flask_cors import CORS
import asyncio
import threading
import logging
import pyaudio
import sys
from pathlib import Path
import threading
base_dir = Path('/stream_custom')
sys.path.append(str(base_dir))

from realtime_audio_uninettuno_ai import RealTimeAudio
class AudioServer:
    
    def __init__(self):
        self.app = Flask(__name__)
        
        CORS(self.app, resources={r"/*": {"origins": "*"}})
        
              
        self.CHUNK_DURATION_MS = 50  # Duration of each chunk in ms
        self.SAMPLE_RATE = 24000       # Sampling rate in Hz
        self.CHANNELS = 1              # Number of audio channels
        self.SAMPLE_WIDTH = 2     
            
        self.FRAMES_PER_CHUNK = int(self.SAMPLE_RATE *self.CHUNK_DURATION_MS / 1000)  # Frames per chunk    
    
        self.realtimeAudio = RealTimeAudio(self.CHUNK_DURATION_MS , self.SAMPLE_RATE, self.CHANNELS,  self.SAMPLE_WIDTH, self.FRAMES_PER_CHUNK)
          
        self.app.add_url_rule('/audio_realtime', view_func=self.communicate_with_user, methods=['POST'])
        
        self.app.add_url_rule('/audio_stop', view_func=self.stop_communication_with_user, methods=['POST'])

    def communicate_with_user(self):
        
        # Setup PyAudio
        self.p = pyaudio.PyAudio()
        
        self.mic_stream = self.p.open(
            format=pyaudio.paInt16,
            channels=self.CHANNELS, 
            rate=self.SAMPLE_RATE,
            input=True,
            stream_callback=self.realtimeAudio.mic_callback,
            frames_per_buffer=self.FRAMES_PER_CHUNK
        )
        print("Listening for speech...")
        try:
            self.mic_stream.start_stream()
            threading.Thread(target=asyncio.run, args=(self.realtimeAudio.connect(),)).start()
           # asyncio.run(self.realtimeAudio.connect())    
            return jsonify({"status":"success"}), 200
        except KeyboardInterrupt:
            print("Stopping...")
            return jsonify({"status":"error"}), 500

    def stop_communication_with_user(self):
        try:
            self.mic_stream.stop_stream()
            self.mic_stream.close()
            self.p.terminate()
            return jsonify({"status":"closed"}), 200
        except KeyboardInterrupt:
            return jsonify({"status":"unexpected issue"}), 500
if __name__=="__main__":
    audioServer = AudioServer()
    audioServer.app.run(debug=True)