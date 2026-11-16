# Build Notes

Folder contains info on how to build for each platform and notes from testing. Below are general notes that apply to all.

# Energy Monitoring

Some on board testing done [in the solar comparison folder](solar%20comparison/readme.md)

# Notes on worker concurrency:

We forced Celery to run serially (worker_concurrency=1) to solve an architectural bottleneck involving **thread deadlocking, CPU thrashing, and memory crashes.**

**Thread Deadlock**
The underlying math engine used by acoupi-batdetect2 is **ONNX Runtime** (backed by libopenblas and torch). These mathematical frameworks are not single-threaded; when they receive an audio file, they automatically spawn an internal pool of threads (known as *intra-op threads*) to split up the heavy matrix algebra across the CPU cores.
If Celery's concurrency is left at its default setting (which matches the number of CPU cores, typically 4), the following clash occurs:
* Celery attempts to process **4 audio files at the exact same time**.
* Each of those 4 files tells ONNX to spawn **4 internal math threads**.
* Suddenly, 16 aggressive mathematical threads are fighting for control of the same physical hardware cores.

Because libopenblas and onnxruntime handle low-level assembly instructions, this massive over-subscription causes a race condition known as a **thread deadlock**. The threads freeze while waiting for each other to release the CPU, completely locking up the background Celery worker. Forcing worker_concurrency=1 ensures only one audio file is processed at a time, allowing ONNX to use the CPU cores harmoniously.

**Eliminating CPU Thrashing** (Context Switching)
From the multi-week power_proxy.log analysis, running a single batdetect2 task pushes the CPU load average to around 0.80→1.16. This means a single file is already maximizing the practical calculation capacity of the system.
If Celery forces the processor to juggle multiple neural network passes concurrently, the operating system spends more time and electrical energy **context switching** (swapping block data in and out of the CPU registers) than it does actually computing bat probabilities. Running them serially (one-by-one) allows a single calculation layer to complete as fast as possible without unnecessary scheduling overhead.

**Protection Against the Out-Of-Memory** 
Deep learning model weights (the neural network layers) take up a massive footprint in system memory when loaded into RAM.
* Loading **one** model instance takes a safe, predictable slice of memory.
* Loading **four** concurrent model pipeline streams exponentially multiplies the heap memory requirements.

On an embedded board, running tasks concurrently will quickly exhaust physical RAM, causing the Linux kernel's OOM (Out-Of-Memory) to step in and forcefully terminate the entire Python virtual environment to protect the operating system from a hard freeze.