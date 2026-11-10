# Gateway Asynchronous Generation and Keyless Local Post-Processing

Generative and creative tasks are submitted as asynchronous jobs to the unified Asset Gateway, whereas deterministic asset transformations (such as resizing, slicing spritesheets, audio trimming/normalization, video frame extraction, and 3D format/mesh inspection) are executed locally without an API key. This avoids unnecessary token cost, network latency, and generative drift when an agent only needs deterministic adjustments to an existing or newly generated asset.
