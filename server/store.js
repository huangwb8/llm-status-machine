import { DATA_DIR, RUNS_DIR, STATES_DIR, storage } from "./storage/index.js";

export { DATA_DIR, RUNS_DIR, STATES_DIR };

export const readStore = storage.readStore;
export const writeStore = storage.writeStore;
export const listCollection = storage.listCollection;
export const getItem = storage.getItem;
export const createItem = storage.createItem;
export const updateItem = storage.updateItem;
export const deleteItem = storage.deleteItem;
export const mutateStore = storage.mutateStore;
export const createRun = storage.createRun;
export const patchRun = storage.patchRun;
export const getSession = storage.getSession;
export const addSession = storage.addSession;
export const patchSession = storage.patchSession;
export const appendSessionEvent = storage.appendSessionEvent;

export function timestamp() {
  return new Date().toISOString();
}
