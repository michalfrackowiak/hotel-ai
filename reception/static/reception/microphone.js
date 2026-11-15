class ReceptionMicrophone extends AudioWorkletProcessor {
    constructor(options) {
        super();
        this.silenceLimit = sampleRate * (options.processorOptions?.silenceSeconds ?? 10);
        this.samples = new Float32Array(Math.round(sampleRate * 14));
        this.preRoll = new Float32Array(Math.round(sampleRate * 0.25));
        this.preRollIndex = 0;
        this.total = 0;
        this.length = 0;
        this.voiced = 0;
        this.quiet = 0;
        this.speaking = false;
    }

    process(inputs) {
        const channel = inputs[0]?.[0];
        if (!channel?.length) return true;

        let energy = 0;
        for (const sample of channel) energy += sample * sample;
        const voiced = Math.sqrt(energy / channel.length) >= 0.012;
        this.total += channel.length;

        if (!this.speaking) {
            for (const sample of channel) {
                this.preRoll[this.preRollIndex] = sample;
                this.preRollIndex = (this.preRollIndex + 1) % this.preRoll.length;
            }
            this.voiced = voiced ? this.voiced + channel.length : 0;
            if (this.voiced >= sampleRate * 0.06) {
                this.speaking = true;
                this.samples.set(this.preRoll.subarray(this.preRollIndex));
                this.samples.set(this.preRoll.subarray(0, this.preRollIndex), this.preRoll.length - this.preRollIndex);
                this.length = this.preRoll.length;
            } else if (this.total >= this.silenceLimit) {
                this.port.postMessage(null);
                return false;
            }
            return true;
        }

        const part = channel.subarray(0, this.samples.length - this.length);
        this.samples.set(part, this.length);
        this.length += part.length;
        this.quiet = voiced ? 0 : this.quiet + part.length;
        if (this.length === this.samples.length || this.quiet >= sampleRate * 0.85) {
            this.port.postMessage(this.samples.subarray(0, this.length), [this.samples.buffer]);
            return false;
        }
        return true;
    }
}

registerProcessor("reception-microphone", ReceptionMicrophone);
