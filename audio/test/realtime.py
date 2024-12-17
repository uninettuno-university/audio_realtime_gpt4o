import pyaudio
import numpy as np
import time

# Audio configuration
CHUNK = 1024  # Number of audio samples per frame
FORMAT = pyaudio.paInt16  # Format for audio input
CHANNELS = 1  # Mono audio
RATE = 44100  # Sample rate (Hz)
THRESHOLD = 1000  # Amplitude threshold to detect speech
SILENCE_DURATION = 4  # Duration (in seconds) to consider as "stop speech"

def detect_speech():
    audio = pyaudio.PyAudio()

    # Open the audio stream
    stream = audio.open(format=FORMAT, 
                        channels=CHANNELS,
                        rate=RATE,
                        input=True,
                        frames_per_buffer=CHUNK)

    print("Listening for speech...")
    speech_detected = False
    silence_start = None

    try:
        while True:
            # Read audio data
            data = stream.read(CHUNK, exception_on_overflow=False)
            # Convert audio data to numpy array
            audio_data = np.frombuffer(data, dtype=np.int16)
            # Calculate amplitude
            amplitude = np.abs(audio_data).mean()

            if amplitude > THRESHOLD:
                if not speech_detected:
                    print(f"Speech started at {time.time()}")
                    speech_detected = True
                silence_start = None  # Reset silence timer
            else:
                if speech_detected:
                    # If silence detected, start the timer
                    if silence_start is None:
                        silence_start = time.time()
                    elif time.time() - silence_start > SILENCE_DURATION:
                        print(f"Speech stopped at {time.time():.2f}")
                        speech_detected = False

    except KeyboardInterrupt:
        print("\nStopping detection.")
    finally:
        # Cleanup
        stream.stop_stream()
        stream.close()
        audio.terminate()

if __name__ == "__main__":
    detect_speech()
