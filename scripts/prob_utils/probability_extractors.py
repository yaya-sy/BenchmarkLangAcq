"""
A large part of this code has been adapted from Tu Anh's work as part of the ZeroSpeech 2021 challenge.
https://github.com/zerospeech/zerospeech2021_baseline
"""
import argparse
from fairseq import tasks, checkpoint_utils
from time import time
import torch
from transformers import BertTokenizer


class ProbExtractor:
    def __init__(self, model_path, dict_path, out_path, batch_size, pooling='mean', gpu=True):
        # set attributes
        self.model_path = model_path
        self.dict_path = dict_path
        if dict_path is None:
            self.dict_path = self.model_path.parent / 'data-bin'
        self.out_path = out_path
        self.batch_size = batch_size
        self.pooling = pooling
        self.model = None
        self.task = None
        self.loaded = False
        self.gpu = gpu
        print(self.model_path)
        # check everything is ok
        if not self.model_path.is_file():
            raise ValueError("%s not found." % self.model_path)
        if not self.dict_path.is_dir():
            raise ValueError("%s not found." % self.dict_path)

        self.load_model()

    def __load_model(self):
        raise NotImplemented()

    def extract(self):
        raise NotImplemented()

    def write_probabilities(self, seq_names, probabilities, out_file):
        out_file.parent.mkdir(exist_ok=True, parents=True)
        with open(out_file, 'w') as f:
            for filename, prob in zip(seq_names, probabilities):
                f.write(f'{filename} {prob}\n')
        print(f'Writing pseudo-probabilities to {out_file}')




class TextLstmProbExtractor(ProbExtractor):
    def __init__(self, model_path, dict_path, out_path, batch_size, remove_word_spaces, bpe_encode=False,
                 bos_eos=False, pooling='mean', gpu=True):
        super().__init__(model_path, dict_path, out_path, batch_size, pooling, gpu)
        self.remove_word_spaces = remove_word_spaces
        self.example_input = None
        self.bpe_encode = bpe_encode
        if bpe_encode:
            self.tokenizer = BertTokenizer.from_pretrained('bert-base-uncased')
        self.bos_eos = bos_eos

    def preprocessing(self, example):
        if self.remove_word_spaces:
            example = example.replace(' <SEP> ', ' ')
        if self.bpe_encode:
            example = ' '.join(self.tokenizer.tokenize(example))
        if self.bos_eos:
            example = '<BOS> ' + example + ' <EOS>'
        return example

    def load_model(self):
        # Set up the args Namespace
        model_args = argparse.Namespace(task='language_modeling', output_dictionary_size=-1,
                                        data=str(self.dict_path), path=str(self.model_path))

        # Setup task
        task = tasks.setup_task(model_args)

        # Load model
        models, _model_args = checkpoint_utils.load_model_ensemble([model_args.path], task=task)
        model = models[0]
        print("Model loaded.")
        if self.gpu:
            model = model.cuda()
        model.eval()
        self.model = model
        self.task = task
        self.loaded = True

    def extract_batch(self, batch):
        if not self.loaded:
            raise ValueError("You should load the model before extracting the probabilities.")
        pad_idx = self.task.source_dictionary.pad()

        # Add start token
        input_sequences = []
        input_lengths = []
        for sequence in batch:
            # Convert from string to list of units
            sequence_tokens = self.task.source_dictionary.encode_line("<s> " + sequence, append_eos=True,
                                                                      add_if_not_exist=False).long()
            input_lengths.append(len(sequence_tokens))
            input_sequences.append(sequence_tokens)

        sequences_inputs = torch.nn.utils.rnn.pad_sequence(input_sequences, batch_first=False,
                                                           padding_value=pad_idx).t()
        if self.gpu:
            sequences_inputs = sequences_inputs.cuda()

        # Compute output: [batch_size,feat_size] --> [batch_size,feat_size,vocab_size]
        output_ts, _ = self.model(sequences_inputs)
        output_ts = output_ts.softmax(dim=-1)

        # Compute proba
        proba_list = []
        for j, sequence in enumerate(batch):
            proba = 0
            for i, ch_idx in enumerate(sequences_inputs[j][1:]):
                score = output_ts[j, i, ch_idx].log()
                proba += score
                if i == input_lengths[j] - 2:
                    break
            if self.pooling == 'mean':
                proba /= input_lengths[j]-1
            proba_list.append(proba.item())
        return proba_list

    def extract_all(self, data):
        seq_names = data['filename']
        transcriptions = data['transcription']
        transcriptions = [self.preprocessing(t) for t in transcriptions]
        print(f'Example input: {transcriptions[0]}')
        self.example_input = transcriptions[0]
        n_batches = len(seq_names) // self.batch_size
        if len(seq_names) % self.batch_size != 0:
            n_batches += 1
        start_time = time()
        probabilities = []
        for i in range(n_batches):
            start_time_batch = time()
            transcriptions_batch = transcriptions[i*self.batch_size:min(len(seq_names), (i+1)*self.batch_size)]
            proba_batch = self.extract_batch(transcriptions_batch)
            probabilities.extend(proba_batch)
            print(f'Done computing batch number %d in %.2f s.' % (i, time()-start_time_batch))
        print(f"Done computing probabilities in %.2f s." % (time()-start_time))
        return seq_names, probabilities

    @property
    def get_example_input(self):
        if self.example_input is None:
            raise ValueError("You should run the extract_all method before asking for an example.")
        return self.example_input