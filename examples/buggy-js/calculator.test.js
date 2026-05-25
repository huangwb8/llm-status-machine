import assert from "node:assert/strict";
import { add, divide } from "./calculator.js";

assert.equal(add(2, 3), 5);
assert.equal(divide(8, 2), 4);
assert.throws(() => divide(4, 0), /division/i);
