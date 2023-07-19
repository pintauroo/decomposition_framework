import pandas as pd
import numpy as np
import os

path = os.getcwd()
dataset = path + '/dataset_stat.csv'


def generate_dataset(entries_num = 100):
    df = pd.read_csv(dataset)

    counts = df['count'].tolist()
    cpu_values = df['num_cpu'].tolist()
    gpu_values = df['num_gpu'].tolist()
    duration_median = df['duration_median'].tolist()
    bandwidth_median = df['bandwidth_median'].tolist()

    

    # Convert counts to a numpy array and normalize it
    counts = np.array(counts)
    probabilities = counts / np.sum(counts)

    # Generate a new dataset with entries proportional to the counts
    selected_entries = np.random.choice(range(len(counts)), size=entries_num, p=probabilities)

    new_dataset = []
    i = 0
    for entry_idx in selected_entries:
        cpu = cpu_values[entry_idx]
        gpu = gpu_values[entry_idx]
        bw = bandwidth_median[entry_idx]
        duration= duration_median[entry_idx]
        new_dataset.append({'job_id': i, 'user': i, 'num_cpu': cpu, 'num_gpu': gpu, 'bw': bw, 'duration': duration})
        i+=1
    new_dataset = pd.DataFrame(new_dataset)
    print(new_dataset)

    return new_dataset



