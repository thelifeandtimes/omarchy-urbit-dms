const assert = require("node:assert/strict")
const fs = require("node:fs")
const vm = require("node:vm")
const context = vm.createContext({})
vm.runInContext(fs.readFileSync("Search.js", "utf8"), context)
const search = (query, rows, contacts) => Array.from(context.find(query, rows, contacts), row => row.ship)
const recipients = (query, rows, contacts) => Array.from(context.recipients(query, rows, contacts), row => ({ship: row.ship, literal: !!row.literal}))

const rows = [
  {ship: "~sampel-palnet", name: "Sam", recency: 50},
  {ship: "~sarlev", name: "Stuart", recency: 100},
]
const contacts = {"~sarlev": "Stuart", "~doznec": "Morgan"}
assert.deepEqual(search("", rows, contacts), ["~sampel-palnet", "~sarlev"])
assert.deepEqual(search("morgna", rows, contacts), ["~doznec"])
assert.deepEqual(search("morgan", rows, contacts), ["~doznec"])
assert.deepEqual(search("~sarle", rows, contacts), ["~sarlev"])
assert.deepEqual(search("sturt", rows, contacts), ["~sarlev"])
assert.deepEqual(search("zzz", rows, contacts), [])
// Clearing an overshot query must restore the inbox; added contacts also show up.
assert.deepEqual(search("", rows, contacts), ["~sampel-palnet", "~sarlev"])
assert.deepEqual(search("~dozn", rows, contacts), ["~doznec"])
const many = Array.from({length: 201}, (_, n) => ({ship: "~peer" + String(n).padStart(3, "0"), name: "Friend " + n, recency: 201 - n}))
assert.equal(search("friend", many, {}).length, 201)
assert.deepEqual(recipients("morgna", rows, contacts), [{ship: "~doznec", literal: false}])
assert.deepEqual(recipients("~sampel-palnet", rows, contacts), [{ship: "~sampel-palnet", literal: false}])
assert.deepEqual(recipients("~zod", rows, contacts), [{ship: "~zod", literal: true}])
assert.equal(recipients("~sarle", rows, contacts).some(row => row.literal), false)
assert.deepEqual(recipients("~sampel-sampel", rows, contacts).at(-1), {ship: "~sampel-sampel", literal: true})
assert.equal(recipients("friend", many, {}).length, 6)
console.log("search tests passed")
