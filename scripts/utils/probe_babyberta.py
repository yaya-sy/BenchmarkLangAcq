# Adapted from: https://github.com/phueb/BabyBERTa/blob/master/babyberta/probing.py

from pathlib import Path
from typing import List, Optional, Union
import numpy as np
import torch
from torch.nn import CrossEntropyLoss

from tokenizers import Tokenizer
from transformers import AutoTokenizer
from transformers.models.roberta import RobertaTokenizer, RobertaTokenizerFast
from transformers.models.roberta import RobertaForMaskedLM
from utils.babyberta.dataset import DataSet, make_sequences
import pandas as pd
from tqdm import tqdm


def load_model(model_name):
    """
    Load tokenizer and model from HuggingFace
    :param model_name:      name of the model that needs to be loaded,
                            must belong to ['BabyBERTa-1', 'BabyBERTa-2', 'BabyBERTa-3']
    :return:                a dictionnary with keys ['tokenizer', 'model']
    """
    assert model_name in ['BabyBERTa-1', 'BabyBERTa-2', 'BabyBERTa-3']
    tokenizer = RobertaTokenizerFast.from_pretrained("phueb/%s" % model_name, add_prefix_space=True)
    model = RobertaForMaskedLM.from_pretrained("phueb/%s" % model_name).cuda()
    model.eval()
    return {'tokenizer': tokenizer, 'model': model}


def babyberta_probing(model, data):
    """
    Probe BabyBERTa model
    :param model:           a dictionnary with keys ['tokenizer', 'model']
    :param data:            a pandas dataframe with columns ['real', 'fake']
    :return:                cross entropies computed by the BabyBERTa model
    """
    # fillna('nan') is to handle the word nan in sWUGGY
    # that gets converted to not a number
    real_stimuli = data.real.fillna('nan').values[:50]
    fake_stimuli = data.fake.values[:50]
    stimuli = np.concatenate((real_stimuli, fake_stimuli))
    stimuli = make_sequences(stimuli, num_sentences_per_input=1)
    stimuli = DataSet.for_probing(stimuli, model['tokenizer'])

    cross_entropies = calc_cross_entropies(model['model'], stimuli)
    cross_entropies = [-c for c in cross_entropies]

    assert len(real_stimuli) + len(fake_stimuli) == len(cross_entropies)
    out = pd.DataFrame({'real': real_stimuli, 'fake': fake_stimuli,
           'real_pp': cross_entropies[:len(real_stimuli)],
           'fake_pp': cross_entropies[-len(fake_stimuli):]})

    # Compute acc across real stimuli, then average
    # For sBLIMP, this will be the classic accuracy
    # For sWUGGY, it will be the accuracy across words
    out['is_model_right'] = out['real_pp'] > out['fake_pp']
    acc_score = (out.groupby('real')['is_model_right'].mean()).sum() / len(out)

    print("Accuracy is: %.2f" % (acc_score * 100))
    return out, acc_score


def calc_cross_entropies(model, dataset):
    """

    :param model:           an instance of RobertaForMaskedLM
    :param dataset:         an instance of DataSet
    :return:                cross entropies computed by the BabyBERTa model
    """
    model.eval()
    cross_entropies = []
    loss_fct = CrossEntropyLoss(reduction='none')

    with torch.no_grad():

        for x, _, _ in tqdm(dataset):
            # get loss
            output = model(**{k: v.to('cuda') for k, v in x.items()})
            logits_3d = output['logits']
            logits_for_all_words = logits_3d.permute(0, 2, 1)
            labels = x['input_ids'].cuda()
            loss = loss_fct(logits_for_all_words,  # need to be [batch size, vocab size, seq length]
                            labels,  # need to be [batch size, seq length]
                            )

            # compute avg cross entropy per sentence
            # to do so, we must exclude loss for padding symbols, using attention_mask
            cross_entropies += [loss_i[np.where(row_mask)[0]].mean().item()
                                for loss_i, row_mask in zip(loss, x['attention_mask'].numpy())]

    if not cross_entropies:
        raise RuntimeError(f'Did not compute cross entropies.')

    return cross_entropies
