import copy
import datetime
from multiprocessing.managers import SyncManager
from multiprocessing import Process, Event, Manager, JoinableQueue
import time
import pandas as pd
pd.set_option('display.max_rows', 500)
import signal
import logging
import os
import sys

from Plebiscito.src.network_topology import NetworkTopology
from Plebiscito.src.topology import topo as LogicalTopology
from Plebiscito.src.network_topology import  TopologyType
from Plebiscito.src.utils import generate_gpu_types, GPUSupport
from Plebiscito.src.node import node
from Plebiscito.src.config import Utility, DebugLevel, SchedulingAlgorithm, ApplicationGraphType
import Plebiscito.src.jobs_handler as job
import Plebiscito.src.utils as utils
import Plebiscito.src.plot as plot
from Plebiscito.src.jobs_handler import message_data

class MyManager(SyncManager): pass

main_pid = ""
nodes_thread = []
TRACE = 5    

def sigterm_handler(signum, frame):
    """Handles the SIGTERM signal by performing cleanup actions and gracefully terminating all processes."""
    # Perform cleanup actions here
    # ...    
    global main_pid
    if os.getpid() == main_pid:
        print("SIGINT received. Performing cleanup...")
        for t in nodes_thread:
            t.terminate()
            t.join()    
            
        print("All processes have been gracefully teminated.")
        sys.exit(0)  # Exit gracefully    

class Simulator_Plebiscito:
    def __init__(self, filename: str, n_nodes: int, n_jobs: int, dataset = pd.DataFrame(), alpha = 1, utility = Utility.LGF, debug_level = DebugLevel.INFO, scheduling_algorithm = SchedulingAlgorithm.FIFO, decrement_factor = 1, split = True, app_type = ApplicationGraphType.LINEAR, enable_logging = False, use_net_topology = False, progress_flag = False, n_client = 0, node_bw = 0, failures = {}, logical_topology = "ring_graph", probability = 0, enable_post_allocation = False) -> None:   
        if utility == Utility.FGD and split:
            print(f"FGD utility and split are not supported simultaneously. Exiting...")
            os._exit(-1)
        
        self.filename = filename + "_" + utility.name + "_" + scheduling_algorithm.name + "_" + str(decrement_factor)
        if split:
            self.filename = self.filename + "_split"
        else:
            self.filename = self.filename + "_nosplit"
            
        if enable_post_allocation:
            self.filename = self.filename + "_rebid"
        else:
            self.filename = self.filename + "_norebid"
            
        self.n_nodes = n_nodes
        self.node_bw = node_bw
        self.n_jobs = n_jobs
        self.n_client = n_client
        self.enable_logging = enable_logging
        self.use_net_topology = use_net_topology
        self.progress_flag = progress_flag
        self.dataset = dataset
        self.debug_level = debug_level
        self.counter = 0
        self.alpha = alpha
        self.scheduling_algorithm = scheduling_algorithm
        self.decrement_factor = decrement_factor
        self.split = split
        self.app_type = app_type
        self.failures = failures
        self.enable_post_allocation = enable_post_allocation
        
        self.job_count = {}
        
        # create a suitable network topology for multiprocessing 
        MyManager.register('NetworkTopology', NetworkTopology)
        MyManager.register('LogicalTopology', LogicalTopology)
        self.physycal_network_manager = MyManager()
        self.physycal_network_manager.start()
        self.logical_network_manager = MyManager()
        self.logical_network_manager.start()
        
        #Build Topolgy
        self.t = self.logical_network_manager.LogicalTopology(func_name=logical_topology, max_bandwidth=node_bw, min_bandwidth=node_bw/2,num_clients=n_client, num_edges=n_nodes, probability=probability)
        self.network_t = self.physycal_network_manager.NetworkTopology(n_nodes, node_bw, node_bw, group_number=4, seed=4, topology_type=TopologyType.FAT_TREE)
        
        self.nodes = []
        self.gpu_types = generate_gpu_types(n_nodes)

        for i in range(n_nodes):
            self.nodes.append(node(i, self.network_t, self.gpu_types[i], utility, alpha, enable_logging, self.t, n_nodes, progress_flag, use_net_topology=use_net_topology, decrement_factor=decrement_factor))
            
        # Set up the environment
        self.setup_environment()
        
    def get_nodes(self):
        return self.nodes
    
    def get_adjacency_matrix(self):
        return copy.deepcopy(self.t.to())
            
    def setup_environment(self):
        """
        Set up the environment for the program.

        Registers the SIGTERM signal handler, sets the main process ID, and initializes logging.
        """
        
        signal.signal(signal.SIGINT, sigterm_handler)
        global main_pid
        main_pid = os.getpid()

        logging.addLevelName(DebugLevel.TRACE, "TRACE")
        logging.basicConfig(filename='debug.log', level=self.debug_level.value, format='%(message)s', filemode='w')

        logging.debug('Clients number: ' + str(self.n_client))
        logging.debug('Edges number: ' + str(self.n_nodes))
        logging.debug('Requests number: ' + str(self.n_jobs))
        
    def setup_nodes(self, terminate_processing_events, start_events, use_queue, manager, return_val, queues, progress_bid_events):
        """
        Sets up the nodes for processing. Generates threads for each node and starts them.
        
        Args:
        terminate_processing_events (list): A list of events to terminate processing for each node.
        start_events (list): A list of events to start processing for each node.
        use_queue (list): A list of events to indicate if a queue is being used by a node.
        manager (multiprocessing.Manager): A multiprocessing manager object.
        return_val (list): A list of return values for each node.
        queues (list): A list of queues for each node.
        progress_bid_events (list): A list of events to indicate progress of bid processing for each node.
        """
        global nodes_thread
        
        for i in range(self.n_nodes):
            q = JoinableQueue()
            e = Event() 
            
            queues.append(q)
            use_queue.append(e)
            
            e.set()

        #Generate threads for each node
        for i in range(self.n_nodes):
            e = Event() 
            e2 = Event()
            e3 = Event()
            return_dict = manager.dict()
            
            self.nodes[i].set_queues(queues, use_queue)
            
            p = Process(target=self.nodes[i].work, args=(e, e2, e3, return_dict))
            nodes_thread.append(p)
            return_val.append(return_dict)
            terminate_processing_events.append(e)
            start_events.append(e2)
            e3.clear()
            progress_bid_events.append(e3)
            
            p.start()
            
        for e in start_events:
            e.wait()
    
    def collect_node_results(self, return_val, jobs: pd.DataFrame, exec_time, time_instant, save_on_file):
        """
        Collects the results from the nodes and updates the corresponding data structures.
        
        Args:
        - return_val: list of dictionaries containing the results from each node
        - jobs: list of job objects
        - exec_time: float representing the execution time of the jobs
        - time_instant: int representing the current time instant
        
        Returns:
        - float representing the utility value calculated based on the updated data structures
        """
        
        if time_instant != 0:
            for _, j in jobs.iterrows():
                self.job_count[j["job_id"]] = 0
                for v in return_val: 
                    nodeId = v["id"]
                
                    self.nodes[nodeId].bids[j["job_id"]] = v["bids"][j["job_id"]]                        
                    self.job_count[j["job_id"]] += v["counter"][j["job_id"]]

            for v in return_val: 
                nodeId = v["id"]
                self.nodes[nodeId].updated_cpu = v["updated_cpu"]
                self.nodes[nodeId].updated_gpu = v["updated_gpu"]
                self.nodes[nodeId].updated_bw = v["updated_bw"]
                self.nodes[nodeId].gpu_type = v["gpu_type"]
        
        return utils.calculate_utility(self.nodes, self.n_nodes, self.counter, exec_time, self.n_jobs, jobs, self.alpha, time_instant, self.use_net_topology, self.filename, self.network_t, self.gpu_types, save_on_file)
    
    def terminate_node_processing(self, events):
        global nodes_thread
        
        for e in events:
            e.set()
            
        # Block until all tasks are done.
        for nt in nodes_thread:
            nt.join()
            
    def clear_screen(self):
        # Function to clear the terminal screen
        os.system('cls' if os.name == 'nt' else 'clear')

    def print_simulation_values(self, time_instant, processed_jobs, queued_jobs: pd.DataFrame, running_jobs, batch_size):
        print()
        print("Infrastructure info")
        print("Last refresh: " + str(datetime.datetime.now()))
        print(f"Number of nodes: {self.n_nodes}")
        
        for t in set(self.gpu_types):
            count = 0
            for i in self.gpu_types:
                if i == t:
                    count += 1
            print(f"Number of {t.name} GPU nodes: {count}")
        
        print()
        print("Performing simulation at time " + str(time_instant) + ".")
        print(f"# Jobs assigned: \t\t{processed_jobs}/{len(self.dataset)}")
        print(f"# Jobs currently in queue: \t{len(queued_jobs)}")
        print(f"# Jobs currently running: \t{running_jobs}")
        print(f"# Current batch size: \t\t{batch_size}")
        print()
        NODES_PER_LINE = 6
        count = 0
        print("Node GPU resource usage")
        for n in self.nodes:
            if count == NODES_PER_LINE:
                count = 0
                print()
            print("Node{0} ({1}):\t{2:3.0f}%\t".format(n.id, n.gpu_type,(n.initial_gpu - n.updated_gpu)/n.initial_gpu*100), end=" |   ")
            count += 1
            #print(f"Node{n.id} ({n.gpu_type}):\t{(n.initial_gpu - n.updated_gpu)/n.initial_gpu*100}%   ", end=" | ")
        print()
        print()
        print("Jobs in queue stats for gpu type:")
        if len(queued_jobs) == 0:
            print("<no jobs in queue>")
        else:
            #print(queued_jobs["gpu_type"].value_counts().to_dict())
            print(queued_jobs[["gpu_type", "num_cpu", "num_gpu"]])
        print()

            
    def print_simulation_progress(self, time_instant, job_processed, queued_jobs, running_jobs, batch_size):
        self.clear_screen()
        self.print_simulation_values(time_instant, job_processed, queued_jobs, running_jobs, batch_size) 
        
    def deallocate_jobs(self, progress_bid_events, queues, jobs_to_unallocate):
        if len(jobs_to_unallocate) > 0:
            for _, j in jobs_to_unallocate.iterrows():
                data = message_data(
                            j['job_id'],
                            j['user'],
                            j['num_gpu'],
                            j['num_cpu'],
                            j['duration'],
                            j['bw'],
                            j['gpu_type'],
                            deallocate=True,
                            split=self.split,
                            app_type=self.app_type
                        )
                for q in queues:
                    q.put(data)

            for e in progress_bid_events:
                e.wait()
                e.clear()  

            return True
        return False     

    def skip_deconfliction(self, jobs): # :)
        if jobs.empty:
            return True
        
        if self.split:
            node_gpu = {}
            node_cpu = {}
            largest_gpu = {}
            largest_cpu = {}
            
            for node in self.nodes:
                gpu_type = GPUSupport.get_gpu_type(node.gpu_type)
                if gpu_type not in node_gpu:
                    node_gpu[gpu_type] = 0
                    node_cpu[gpu_type] = 0
                    largest_cpu[gpu_type] = 0
                    largest_gpu[gpu_type] = 0
                    
                node_gpu[gpu_type] += node.get_avail_gpu()  # Consider caching these values if they don't change
                node_cpu[gpu_type] += node.get_avail_cpu()

                if node.get_avail_cpu() > largest_cpu[gpu_type]:
                    largest_cpu[gpu_type] = node.get_avail_cpu()
                if node.get_avail_gpu() > largest_gpu[gpu_type]:
                    largest_gpu[gpu_type] = node.get_avail_gpu()
        
        for _, row in jobs.iterrows():
            num_gpu = row['num_gpu']
            num_cpu = row['num_cpu']
            
            if self.split:
                # TODO: improve using the largest_cpu and the largest_gpu info
                gpu_type = GPUSupport.get_gpu_type(row["gpu_type"])
                for k in node_gpu:
                    if GPUSupport.can_host(k, gpu_type):
                        if node_cpu[gpu_type] >= num_cpu and node_gpu[gpu_type] >= num_gpu:
                            print(f"Job {row['job_id']} [{row['gpu_type']}] can be dispatched. Req: {row['num_cpu']} ({node_cpu[gpu_type]}) CPU. Req: {row['num_gpu']} ({node_gpu[gpu_type]}) GPU.")
                            return False
                        #else:
                        #    print(f"Job {row['job_id']} can't be dispatched. Req: {row['num_cpu']} ({node_cpu[gpu_type]}) CPU. Req: {row['num_gpu']} ({node_gpu[gpu_type]}) GPU.")
            else:
                for node in self.nodes:           
                    if GPUSupport.can_host(GPUSupport.get_gpu_type(node.gpu_type), GPUSupport.get_gpu_type(row["gpu_type"])) and node.get_avail_cpu() >= num_cpu and node.get_avail_gpu() >= num_gpu:
                        print(f"Job {row['job_id']} [{row['gpu_type']}] can be dispatched. Req: {row['num_cpu']} ({node.get_avail_cpu()}) CPU. Req: {row['num_gpu']} ({node.get_avail_gpu()}) GPU.")
                        return False
                        # dispatch.append(row)
                        # break
        return True
        # return pd.DataFrame(dispatch) if dispatch else None
        
    def detach_node(self, nodeid):
        self.t.detach_node(nodeid)

    def run(self):
        # Set up nodes and related variables
        global nodes_thread
        terminate_processing_events = []
        start_events = []
        progress_bid_events = []
        use_queue = []
        manager = Manager()
        return_val = []
        queues = []
        self.setup_nodes(terminate_processing_events, start_events, use_queue, manager, return_val, queues, progress_bid_events)

        # Initialize job-related variables
        self.job_ids=[]
        jobs = pd.DataFrame()
        running_jobs = pd.DataFrame()
        processed_jobs = pd.DataFrame()

        # Collect node results
        start_time = time.time()
        self.collect_node_results(return_val, pd.DataFrame(), time.time()-start_time, 0, save_on_file=True)
        
        time_instant = 1
        batch_size = 1
        jobs_to_unallocate = pd.DataFrame()
        unassigned_jobs = pd.DataFrame()
        assigned_jobs = pd.DataFrame()
        prev_job_list = pd.DataFrame()
        curr_job_list = pd.DataFrame()
        prev_running_jobs = pd.DataFrame()
        curr_running_jobs = pd.DataFrame()
        jobs_report = pd.DataFrame()
        job_allocation_time = []
        job_post_process_time = []
        done = False
        
        while not done:
            start_time = time.time()
            
            # Extract completed jobs
            if len(running_jobs) > 0:
                running_jobs["current_duration"] = running_jobs["current_duration"] + running_jobs["speedup"]
                prev_running_jobs = list(running_jobs["job_id"])
                
            jobs_to_unallocate, running_jobs = job.extract_completed_jobs(running_jobs, time_instant)
            # print(jobs_to_unallocate)
            
            jobs_report = pd.concat([jobs_report, jobs_to_unallocate])
            
            # Deallocate completed jobs
            self.deallocate_jobs(progress_bid_events, queues, jobs_to_unallocate)                
            self.collect_node_results(return_val, pd.DataFrame(), time.time()-start_time, time_instant, save_on_file=False)
            
            if len(running_jobs) > 0:
                curr_running_jobs = list(running_jobs["job_id"])
            
            id = -1
            if bool(self.failures):
                for i in range(len(self.failures["time"])):
                    if time_instant == self.failures["time"][i]:
                        id = self.failures["nodes"][i]
                        break
                if id != -1:
                    self.detach_node(id)
                    
            #if time_instant%1000 == 0:
            #    plot.plot_all(self.n_nodes, self.filename, self.job_count, self.filename, job_allocation_time, job_post_process_time)
                    
            # Select jobs for the current time instant
            new_jobs = job.select_jobs(self.dataset, time_instant)
            
            # Add new jobs to the job queue
            if len(jobs) > 0:
                prev_job_list = list(jobs["job_id"])
                
            jobs = pd.concat([jobs, new_jobs], sort=False)
            
            # Schedule jobs
            jobs = job.schedule_jobs(jobs, self.scheduling_algorithm)
            
            if len(jobs) > 0:
                curr_job_list = list(jobs["job_id"])
            
            n_jobs = len(jobs)
            # if prev_job_list.equals(jobs) and prev_running_jobs.equals(running_jobs):
            #     n_jobs = 0
            # if sorted(prev_job_list) == sorted(curr_job_list) and sorted(prev_running_jobs) == sorted(curr_running_jobs):
            #     n_jobs = 0
            
            jobs_to_submit = job.create_job_batch(jobs, n_jobs)
            
            unassigned_jobs = pd.DataFrame()
            assigned_jobs = pd.DataFrame()
            
            # Dispatch jobs
            if len(jobs_to_submit) > 0: 
                start_id = 0
                while start_id < len(jobs_to_submit):
                    subset = jobs_to_submit.iloc[start_id:start_id+batch_size]

                    # if self.skip_deconfliction(subset) == False:
                    t = time.time()
                    self.dispatch_jobs(progress_bid_events, queues, subset) 
                        
                    job_allocation_time.append(time.time()-t)
                    logging.log(TRACE, 'All nodes completed the processing...')
                    exec_time = time.time() - start_time
                
                    t = time.time()
                    # Collect node results
                    a_jobs, u_jobs = self.collect_node_results(return_val, subset, exec_time, time_instant, save_on_file=False)
                    job_post_process_time.append(time.time() - t)
                    assigned_jobs = pd.concat([assigned_jobs, pd.DataFrame(a_jobs)])
                    unassigned_jobs = pd.concat([unassigned_jobs, pd.DataFrame(u_jobs)])
                
                    # Deallocate unassigned jobs
                    self.deallocate_jobs(progress_bid_events, queues, pd.DataFrame(u_jobs))
                    self.collect_node_results(return_val, pd.DataFrame(), time.time()-start_time, time_instant, save_on_file=False)
                    # else:
                    #     unassigned_jobs = pd.concat([unassigned_jobs, subset])
                        #print('ktm')
                        
                    start_id += batch_size
                    
            # Assign start time to assigned jobs
            assigned_jobs = job.assign_job_start_time(assigned_jobs, time_instant)
            
            # Add unassigned jobs to the job queue
            jobs = pd.concat([jobs, unassigned_jobs], sort=False)  
            running_jobs = pd.concat([running_jobs, assigned_jobs], sort=False)
            processed_jobs = pd.concat([processed_jobs,assigned_jobs], sort=False)
            
            unassigned_jobs = pd.DataFrame()
            assigned_jobs = pd.DataFrame()

            if self.enable_post_allocation:
                if time_instant%50 == 0:
                    low_speedup_threshold = 1
                    high_speedup_threshold = 1.3
                                
                    jobs_to_reallocate, running_jobs = job.extract_rebid_job(running_jobs, low_thre=low_speedup_threshold, high_thre=high_speedup_threshold, duration_therehold=250)
                                
                    if len(jobs_to_reallocate) > 0: 
                        start_id = 0
                        while start_id < len(jobs_to_reallocate):
                            subset = jobs_to_reallocate.iloc[start_id:start_id+batch_size]
                            self.deallocate_jobs(progress_bid_events, queues, subset)
                            print(f"Job deallocated {float(subset['speedup'])}")
                            self.dispatch_jobs(progress_bid_events, queues, subset, check_speedup=True, low_th=low_speedup_threshold, high_th=high_speedup_threshold) 
                            
                            a_jobs, u_jobs = self.collect_node_results(return_val, subset, exec_time, time_instant, save_on_file=False)
                            assigned_jobs = pd.concat([assigned_jobs, pd.DataFrame(a_jobs)])
                            unassigned_jobs = pd.concat([unassigned_jobs, pd.DataFrame(u_jobs)])
                            print(f"Job dispatched {float(pd.DataFrame(a_jobs)['speedup'])}")
                            start_id += batch_size
                            
            jobs = pd.concat([jobs, unassigned_jobs], sort=False)  
            running_jobs = pd.concat([running_jobs, assigned_jobs], sort=False)
            
            self.collect_node_results(return_val, pd.DataFrame(), time.time()-start_time, time_instant, save_on_file=True)
            
            self.print_simulation_progress(time_instant, len(processed_jobs), jobs, len(running_jobs), batch_size)
            time_instant += 1

            # Check if all jobs have been processed
            # if len(processed_jobs) == len(self.dataset) and len(running_jobs) == 0 and len(jobs) == 0: # add to include also the final deallocation
            if len(processed_jobs) == len(self.dataset) and len(jobs) == 0: # add to include also the final deallocation
                print('!!!last allocated', time_instant)
                job.extract_allocated_jobs(processed_jobs, self.filename + "_allocations.csv")

                done=True
                # break
            # else:
            #     print('still left', time_instant, len(processed_jobs), len(self.dataset), len(running_jobs), len(jobs))
            #     print(jobs)
        
        # Collect final node results
        self.collect_node_results(return_val, pd.DataFrame(), time.time()-start_time, time_instant+1, save_on_file=True)
        
        self.print_simulation_progress(time_instant, len(processed_jobs), jobs, len(running_jobs), batch_size)
        
        # Terminate node processing
        self.terminate_node_processing(terminate_processing_events)

        # Save processed jobs to CSV
        jobs_report.to_csv(self.filename + "_jobs_report.csv")

        # Plot results
        if self.use_net_topology:
            self.network_t.dump_to_file(self.filename, self.alpha)

    def rebid(self, progress_bid_events, return_val, queues, running_jobs, time_instant, batch_size, unassigned_jobs, assigned_jobs, exec_time):
        low_speedup_threshold = 1
        high_speedup_threshold = 1.2
                    
        jobs_to_reallocate, running_jobs = job.extract_rebid_job(running_jobs, low_thre=low_speedup_threshold, high_thre=high_speedup_threshold, duration_therehold=500)
                    
        if len(jobs_to_reallocate) > 0: 
            start_id = 0
            while start_id < len(jobs_to_reallocate):
                subset = jobs_to_reallocate.iloc[start_id:start_id+batch_size]
                self.deallocate_jobs(progress_bid_events, queues, subset)
                print("Job deallocated")
                self.dispatch_jobs(progress_bid_events, queues, subset, check_speedup=True, low_th=low_speedup_threshold, high_th=high_speedup_threshold) 
                print("Job dispatched")
                a_jobs, u_jobs = self.collect_node_results(return_val, subset, exec_time, time_instant, save_on_file=False)
                assigned_jobs = pd.concat([assigned_jobs, pd.DataFrame(a_jobs)])
                unassigned_jobs = pd.concat([unassigned_jobs, pd.DataFrame(u_jobs)])
                start_id += batch_size
        return running_jobs,unassigned_jobs,assigned_jobs

        #plot.plot_all(self.n_nodes, self.filename, self.job_count, "plot")

    def dispatch_jobs(self, progress_bid_events, queues, subset, check_speedup=False, low_th=1, high_th=1.2):
        job.dispatch_job(subset, queues, self.use_net_topology, self.split, check_speedup=check_speedup, low_th=low_th, high_th=high_th)

        for e in progress_bid_events:
            e.wait()
            e.clear()

    
