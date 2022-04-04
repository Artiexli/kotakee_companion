#
# multispeaker_synthesis_utility.py
#
# "Production" utilization of the generated multispeaker synthesis
# models. Utilizes models saved in the local "model" folder to generate
# output audio combining speaker attributes extracted from reference
# audio of a target speaker and output text. 
#
# Allows for integration with an Emotion Detection model as an upstream 
# component, integrating an "emotion prior" into the output audio by
# selectively choosing the input reference audio depending on the
# provided emotion label. This allows for a more "involved" output
# relevant to the content of the text itself. 

from typing import List
from pathlib import Path
import time
import os
import sys
import wave
import pyaudio  
import re
import random

class MultispeakerSynthesisUtility:
  # Given model variants location, how do we get to synthesizer models? 
  _model_variants_synthesizer_subpath = "synthesizer"
  _model_suffix = ".pt"

  # For the sake of simplicity, we simply save the wav file with a temp
  # filename in this location. 
  _temp_wav_name = "temp_multispeaker_synthesis.wav"

  _inference_class = None
  _inference = None

  _split_sentence_re = r'[\.|!|,|\?|:|;|-|\n] '

  # Upon initialization, attempt to load the model specified.
  # Allow user to provide model location and override the default.
  def __init__(self, model_num, model_variants_location, speakers_location, 
               inference_location, inference_class_name):
    print("[DEBUG] MultispeakerSynthesisUtility - Initializing model variant "+str(model_num)+"...")

    self.speakers_location = speakers_location

    # Get the first file ending in .pt for the synthesizer. 
    synthesizer_model_fpath = self._get_model_fpath(model_variants_location, 
                                                    self._model_variants_synthesizer_subpath,
                                                    model_num)
    if synthesizer_model_fpath is None:
      print("[ERROR] MultispeakerSynthesisUtility - Could not find a valid %s file in %s!" % (self._model_suffix, synthesizer_model_fpath))
    else:
      # Load the Synthesizer inference class, located in the
      # multispeaker_synthesis repo. 
      print("[DEBUG] MultispeakerSynthesisUtility - Importing synthesizer inference class.")
      self._inference_class = self.load_class(module_name=inference_location, 
                                              class_name=inference_class_name)
      if self._inference_class is None:
        print("[ERROR] MultispeakerSynthesisUtility - Failed to import synthesizer inference class!")
      else:
        # Initialize the inference class. Load the model immediately. 
        self._inference = self._inference_class(synthesizer_fpath = synthesizer_model_fpath,
                                                verbose = True,
                                                load_immediately = True)
        print("[DEBUG] MultispeakerSynthesisUtility - Load complete.")

  # Given the location of the model variants, the subpath, and the
  # model name, grab the first file with the appropriate suffix in
  # the directory. 
  def _get_model_fpath(self, model_variants_location, subpath, model_num):
    model_fpath = model_variants_location + "/" + subpath + "/" + model_num
    model_variant_files = os.listdir(model_fpath)
    model_filename = None
    for file in model_variant_files:
      if file.endswith(self._model_suffix):
        model_filename = model_fpath + "/" + file
        break
    return Path(model_filename)

  # Principal function. Given a list of strings to utter, the name
  # of the speaker, and the name of the wav file, finds the speaker
  # embedding (or wav file if it can't). The utternace id should not
  # have a file extension.
  #
  # Synthesizes audio accordingly and then plays the audio in a 
  # self-contained manner. If there are multiple generated wavs, 
  # they will be played one after the other. 
  #
  # Blocking function - will end when the wavs finish.
  #
  # Can interface with Emotion Detection upstream model. Given
  # an emotion category, choose an embed file from the specified
  # directory of the target speaker speaking with that emotion. 
  # This "injects" the inferred emotion prior into our output.
  def speaker_synthesize_speech(self, texts: List[str],
                                speaker_id: str,
                                utterance_id: str):
    start_time = time.time()

    # Preprocess the texts (on this end)
    texts = self._preprocess_texts(texts)

    if speaker_id.lower() == "random":
      speaker_id = self.random_speaker()
      print("[DEBUG] MultispeakerSynthesisUtility - RANDOM speaker: %s" % speaker_id)

    # Get the filepath to the wav or embedding for this utterance.
    #utterance_location = self.speakers_location + "/" + speaker_id + "/" + utterance_id
    utterance_location = self.speakers_location + "/" + speaker_id
    
    embeds_fpath = Path(utterance_location + ".npy")
    if embeds_fpath.exists():
      # Use the embedding. 
      wavs = self._synthesize_audio_from_embeds(texts = texts, embeds_fpath=embeds_fpath)
    else:
      wav_fpath = Path(utterance_location + ".wav")
      if not wav_fpath.exists():
        print("[ERROR] MultispeakerSynthesisUtility - Unable to find embeds or wav file for path: %s" % utterance_location)
        return
      wavs = self._synthesize_audio_from_audio(texts = texts, wav_fpath = wav_fpath)

    print("[DEBUG] MultispeakerSynthesisUtility - Completed in %.4f seconds." % (time.time() - start_time))
    
    return wavs

  # Given a list of npy arrays, write each wav into a temporry file
  # and execute them one after the other. 
  def play_wav(self, wavs):
    # Helper function so we make sure pyaudio releases the wav file
    # so we can delete it.
    def play_wavs(self, wavs):
      p = pyaudio.PyAudio()

      for wav in wavs:
        self._inference.save_wav(wav, self._temp_wav_name)

        chunk = 1024
        p = pyaudio.PyAudio()
        f = wave.open(self._temp_wav_name, "rb")
        stream = p.open(format = p.get_format_from_width(f.getsampwidth()),  
                    channels = f.getnchannels(),  
                    rate = f.getframerate(),  
                    output = True) 
        data = f.readframes(chunk)
        while data:  
          stream.write(data)  
          data = f.readframes(chunk)
        stream.stop_stream()  
        stream.close()  

      #close PyAudio  
      p.terminate()

    play_wavs(self, wavs)

    # Delete the last temporary wav file. 
    if os.path.exists(self._temp_wav_name):
      os.remove(self._temp_wav_name)

  def check_speaker_exists(self, new_speaker):
    """
    Allows for the user to select a new speaker. Returns True if the
    speaker was found, False otherwise. 

    We don't actually change anything - we just check that it exists. 
    """
    return os.path.exists(self.speakers_location + "/" + new_speaker + ".npy")

  def list_all_speakers(self):
    speaker_names = []
    for item in os.listdir(self.speakers_location):
      if item.endswith(".npy"):
        speaker_names.append(item.replace(".npy", ""))
    return speaker_names

  def random_speaker(self):
    speaker_list = self.list_all_speakers()
    i = random.randrange(0, len(speaker_list)-1)
    speaker_id = speaker_list[i]
    return speaker_id

  def replace_common_misdetections(self, new_speaker):
    """
    Google SR doesn't understand a lot of these names.
    """
    if new_speaker == "LILA": new_speaker = "LAILAH"
    elif new_speaker == "ALICIA": new_speaker = "ALISHA"
    elif new_speaker == "ARTORIAS": new_speaker = "ARTORIUS"
    elif new_speaker == "FENWICK": new_speaker = "BENWICK"
    elif new_speaker == "BIEN PHU": new_speaker = "BIENFU"
    elif new_speaker == "DAZZLE": new_speaker = "DEZEL"
    elif new_speaker == "TERCEL": new_speaker = "DRISELLE"
    elif new_speaker == "DIAL": new_speaker = "DYLE"
    elif new_speaker == "AISIN": new_speaker = "EIZEN"
    elif new_speaker == "ELLIE'S": new_speaker = "ELIZE"
    elif new_speaker == "CRIM WIRE": new_speaker = "GRIMOIRH"
    elif new_speaker == "GRIMOIRE": new_speaker = "GRIMOIRH"
    elif new_speaker == "WANNA": new_speaker = "KAMOANA"
    elif new_speaker == "CAROL": new_speaker = "KAROL"
    elif new_speaker == "CORRIGAN": new_speaker = "KUROGANE"
    elif new_speaker == "MY LOU": new_speaker = "MAGILOU"
    elif new_speaker == "MAGGIE LOU": new_speaker = "MAGILOU"
    elif new_speaker == "CORRIGAN": new_speaker = "KUROGANE"
    elif new_speaker == "MEDUSA": new_speaker = "MEDISSA"
    elif new_speaker == "MODESTA": new_speaker = "MEDISSA"
    elif new_speaker == "MAKE LEO": new_speaker = "MIKLEO"
    elif new_speaker == "MILA": new_speaker = "MILLA"
    elif new_speaker == "MUSIC": new_speaker = "MUZET"
    elif new_speaker == "ROKU": new_speaker = "ROKUROU"
    elif new_speaker == "ROWING": new_speaker = "ROWEN"
    elif new_speaker == "SARAH'S": new_speaker = "SERES"
    elif new_speaker == "SERIES": new_speaker = "SERES"
    elif new_speaker == "SARAY": new_speaker = "SOREY"
    elif new_speaker == "SURAE": new_speaker = "SOREY"
    elif new_speaker == "TIVO": new_speaker = "TEEPO"
    elif new_speaker == "URI": new_speaker = "YURI"
    elif new_speaker == "THE FEET": new_speaker = "ZAVEID" # lol...
    elif new_speaker == "DAVID": new_speaker = "ZAVEID"
    return new_speaker

  # Given a list of strings to turn into wavs as wella st he path to
  # the wav file for the speaker utterance, reutrn a list of synthesized
  # wavs.
  def _synthesize_audio_from_audio(self, texts: List[str], 
                                  wav_fpath: Path):
    print("[DEBUG] MultispeakerSynthesisUtility - Embedding speaker representation + synthesizing audio for input text:")
    print(texts)
    wavs = self._inference.synthesize_audio_from_audio(texts, wav_fpath)
    return wavs

  # Given a list of strings to turn into wavs as well as the path to
  # the embedding of the speaker, return a list of synthesized wavs.
  def _synthesize_audio_from_embeds(self, texts: List[str], 
                                   embeds_fpath: Path):
    print("[DEBUG] MultispeakerSynthesisUtility - Synthesizing audio with speaker embedding for input text:")
    print(texts)
    wavs = self._inference.synthesize_audio_from_embeds(texts, embeds_fpath)
    return wavs

  # The only preprocessing we do here is to split sentences into
  # different strings. This makes the pronunciation cleaner and also
  # makes inference faster (as it happens in a batch).
  def _preprocess_texts(self, texts):
    processed_texts = []
    for text in texts:
      split_text = re.split(self._split_sentence_re, text)
      processed_texts += split_text
    return processed_texts

  # Dynamic class import. Changes sys.path to navigate directories
  # if necessary. Utilized for emotion detection and
  # representation classes. 
  #
  # Expects module_name Ex) ./home_automation/home_automation
  # and class_name Ex) HomeAutomation
  def load_class(self,  module_name, class_name):
    module = None
    imported_class = None
    module_file_name = None

    # Ex) ./home_automation - split by last slash. 
    # Don't bother if the original file is not within a subdirectory.
    split_module_name = module_name.rsplit("/", 1)
    module_folder_path = split_module_name[0]
    if(module_folder_path != "." and len(split_module_name) > 1):
      sys.path.append(module_folder_path)
      module_file_name = split_module_name[1]
    else:
      module_file_name = module_name.replace("./", "")

    # Fetch the module first.
    try:
      module = __import__(module_file_name)
    except:
      print("[ERROR] Failed to import module " + module_file_name + " from subdirectory '" + module_folder_path + "'.")
      return None

    # Return the class. 
    try:
      imported_class = getattr(module, class_name)
    except:
      print("[ERROR] Failed to import class_name " + class_name + ".")
      return None

    return imported_class

# For debug purposes only. 
if __name__ == "__main__":
  model_variants_location = "../../../multispeaker_synthesis/production_models"
  speakers_location = "../../../multispeaker_synthesis_speakers"
  inference_location = "../../../multispeaker_synthesis/production_inference"
  inference_class_name = "MultispeakerSynthesis"
  model_num = "model1"

  texts = ["Hello world from Kotakee Companion! How are you doing today\nI'm feeling good."]
  speaker_id = "ELEANOR"
  #speaker_id = "ELEANOR_OLD"
  utterance_id = "neutral"

  utility = MultispeakerSynthesisUtility(model_num=model_num, 
                                         model_variants_location=model_variants_location, 
                                         speakers_location=speakers_location,
                                         inference_location=inference_location,
                                         inference_class_name=inference_class_name)
  
  wavs = utility.speaker_synthesize_speech(texts=texts, speaker_id=speaker_id, utterance_id=utterance_id)

  utility.play_wav(wavs)