import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import { AudioCapturePipeline } from "../src/audio/AudioCapturePipeline.ts";
import { MeetingSocket } from "../src/ws/MeetingSocket.ts";

const contexts = [];
class Context {
  state = "suspended";
  destination = {};
  audioWorklet = { addModule: async () => {} };
  constructor() { contexts.push(this); }
  async resume() { this.state = "running"; }
  async close() { this.state = "closed"; }
  createMediaStreamSource(stream) { this.stream = stream; return { connect() {}, disconnect() {} }; }
  createGain() { return { gain: {}, connect() {} }; }
}
class Worklet {
  port = {};
  connect() { return { connect() {} }; }
  disconnect() {}
}
globalThis.AudioContext = Context;
globalThis.AudioWorkletNode = Worklet;

test("both contexts resume before waiting for media permissions", async () => {
  let allow;
  const permission = new Promise((resolve) => { allow = resolve; });
  const streams = [{ source: "tab" }, { source: "mic" }];
  const pipelines = streams.map(() => new AudioCapturePipeline(() => {}));
  const starts = pipelines.map((pipeline, index) => pipeline.start({
    start() {
      assert.equal(contexts[index].state, "running");
      return permission.then(() => streams[index]);
    },
  }));
  assert.equal(contexts.length, 2);
  assert.ok(contexts.every((context) => context.state === "running"));
  allow();
  await Promise.all(starts);
  assert.equal(contexts[0].stream, streams[0]);
  assert.equal(contexts[1].stream, streams[1]);
  pipelines.forEach((pipeline) => pipeline.stop());
  assert.ok(contexts.every((context) => context.state === "closed"));
});

test("worklet sends nonzero PCM using its own transfer buffer, socket preserves source framing", async () => {
  const chunks = [];
  let Processor;
  vm.runInNewContext(readFileSync(new URL("../public/pcm-worklet.js", import.meta.url), "utf8"), {
    AudioWorkletProcessor: class {
      port = { postMessage(buffer, transfer) {
        assert.equal(transfer[0], buffer);
        chunks.push(structuredClone(buffer, { transfer }));
      } };
    },
    registerProcessor(_name, implementation) { Processor = implementation; },
  });
  const processor = new Processor();
  for (let i = 0; i < 8; i++) processor.process([[new Float32Array(128).fill(0.5)]]);
  assert.equal(chunks.length, 2);
  assert.equal(chunks[0].byteLength, 1024);
  assert.ok(new Int16Array(chunks[0]).every((sample) => sample === 16383));
  const sent = [];
  globalThis.WebSocket = class {
    static OPEN = 1;
    readyState = 1;
    constructor() { queueMicrotask(() => this.onopen()); }
    send(data) { sent.push(data); }
    close() {}
  };
  const socket = new MeetingSocket("ws://test");
  await socket.connect();
  socket.sendAudioChunk(chunks[0], 0);
  socket.sendAudioChunk(chunks[1], 1);
  assert.deepEqual(sent.map((frame) => new Uint8Array(frame)[0]), [0, 1]);
  for (const frame of sent) {
    assert.equal(frame.byteLength, 1025);
    assert.deepEqual(new Uint8Array(frame).slice(1), new Uint8Array(chunks[0]));
  }
});
