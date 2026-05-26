import { nanoid } from "nanoid";
import { timestamp } from "./store.js";

const CHANNEL = process.env.EVENT_BUS_CHANNEL || "llm-status-machine:events";

export function createEventBus({ driver = process.env.EVENT_BUS || "memory", redisUrl = process.env.REDIS_URL } = {}) {
  const listeners = new Set();
  let publisherPromise = null;
  let subscriberPromise = null;

  async function createRedis() {
    const { default: Redis } = await import("ioredis");
    const options = {
      maxRetriesPerRequest: null,
      enableReadyCheck: false
    };
    return redisUrl ? new Redis(redisUrl, options) : new Redis(options);
  }

  async function getPublisher() {
    if (!publisherPromise) publisherPromise = createRedis();
    return publisherPromise;
  }

  async function ensureSubscriber() {
    if (driver !== "redis" || subscriberPromise) return;
    subscriberPromise = createRedis();
    const subscriber = await subscriberPromise;
    subscriber.on("message", (_channel, message) => {
      try {
        const event = JSON.parse(message);
        for (const listener of listeners) listener(event);
      } catch (error) {
        console.error("Failed to parse Redis event", error);
      }
    });
    await subscriber.subscribe(CHANNEL);
  }

  function publishLocal(event) {
    for (const listener of listeners) listener(event);
  }

  async function broadcast(event) {
    if (driver === "redis") {
      const publisher = await getPublisher();
      await publisher.publish(CHANNEL, JSON.stringify(event));
      return event;
    }
    publishLocal(event);
    return event;
  }

  async function emit(runId, sessionId, type, payload) {
    return broadcast({
      id: nanoid(10),
      runId,
      sessionId,
      type,
      payload,
      ts: timestamp()
    });
  }

  function subscribe(listener) {
    listeners.add(listener);
    ensureSubscriber().catch((error) => console.error("Failed to subscribe to Redis events", error));
    return () => listeners.delete(listener);
  }

  async function close() {
    const clients = await Promise.all([publisherPromise, subscriberPromise].filter(Boolean));
    await Promise.all(clients.map((client) => client.quit()));
  }

  return { emit, broadcast, subscribe, close };
}

export const eventBus = createEventBus();
export const emit = eventBus.emit;
export const broadcast = eventBus.broadcast;
export const subscribe = eventBus.subscribe;
