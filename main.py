from src.simulator import Simulator
from src.config import Utility, DebugLevel, SchedulingAlgorithm
from src.dataset_builder import generate_dataset

if __name__ == '__main__':
    n_jobs = 200
    dataset = generate_dataset(entries_num=n_jobs)
    
    simulator = Simulator(filename="prova",
                          n_nodes=50,
                          node_bw=1000000000,
                          n_jobs=n_jobs,
                          n_client=3,
                          enable_logging=False,
                          use_net_topology=False,
                          progress_flag=False,
                          dataset=dataset,
                          alpha=1,
                          utility=Utility.LGF,
                          debug_level=DebugLevel.INFO,
                          scheduling_algorithm=SchedulingAlgorithm.FIFO,
                          decrement_factor=0.1)
    simulator.run()