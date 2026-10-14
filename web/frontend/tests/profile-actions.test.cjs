const assert = require("node:assert/strict");
const { test } = require("node:test");
const { join } = require("node:path");
const React = require("react");
const { useProfileActions } = require(join(process.env.VEILWAY_AUTH_MODULES, "useProfileActions.js"));
const { profilesApi, ApiError } = require(join(process.env.VEILWAY_AUTH_MODULES, "api.js"));
async function harness(check) {
 const originals = { useState: React.useState, useRef: React.useRef, api: {...profilesApi} };
 const cells=[]; let index=0, logouts=0, refreshes=0;
 React.useState = initial => { const i=index++; if (!(i in cells)) cells[i]=typeof initial === "function" ? initial() : initial;
  return [cells[i], value => {cells[i]=typeof value === "function" ? value(cells[i]) : value;}]; };
 React.useRef = initial => {const i=index++; if (!(i in cells)) cells[i]={current:initial}; return cells[i];};
 const render = () => {index=0; return useProfileActions({session:{role:"ADMIN",csrf_token:"synthetic-csrf"},onLogout:()=>logouts++,refresh:async()=>{refreshes++;}});};
 try {await check(render,()=>({logouts,refreshes}));} finally {React.useState=originals.useState;React.useRef=originals.useRef;Object.assign(profilesApi,originals.api);}
}
const event={preventDefault(){}};
const profile={id:"synthetic-profile",device_name:"Synthetic device",status:"active",expires_at:"2099-01-01T00:00:00Z"};
test("create retry preserves the exact sent payload and idempotency key", async()=>harness(async render=>{
 const calls=[]; profilesApi.create=async(payload,csrf)=>{calls.push([payload,csrf]);if(calls.length===1)throw new ApiError(503,"ignored");};
 let actions=render();actions.setDraft(d=>({...d,device_name:" First "}));actions=render();await actions.create(event);
 actions=render();actions.setDraft(d=>({...d,device_name:"edited",key:"different"}));actions=render();await actions.create(event);
 assert.equal(calls.length,2);assert.equal(calls[0][0],calls[1][0]);assert.equal(calls[1][0].device_name,"First");assert.equal(calls[1][1],"synthetic-csrf");
}));
test("in-flight create blocks another create and download",async()=>harness(async render=>{
 let resolve,calls=0;profilesApi.create=()=>{calls++;return new Promise(r=>resolve=r);};profilesApi.download=()=>{throw Error("overlap");};
 const actions=render();const pending=actions.create(event);await actions.create(event);await actions.download(profile);assert.equal(calls,1);resolve();await pending;
 assert.equal(render().busy,"");
}));
test("revoke retry retains its key and refreshes only on success",async()=>harness(async(render,counts)=>{
 const calls=[];profilesApi.revoke=async(...args)=>{calls.push(args);if(calls.length===1)throw new ApiError(503,"ignored");};
 render().openAction("revoke",profile);await render().mutate(event);await render().mutate(event);
 assert.deepEqual(calls[0],calls[1]);assert.equal(calls[1][2],"synthetic-csrf");assert.equal(counts().refreshes,1);assert.equal(render().action,null);
}));
test("expired revoke is rejected before reaching the API",async()=>harness(async render=>{
 profilesApi.revoke=()=>{throw Error("expired request");};render().openAction("revoke",{...profile,expires_at:"2000-01-01T00:00:00Z"});await render().mutate(event);
 assert.match(render().formError,/истёк/);
}));
test("401 triggers logout for download, create and mutation",async()=>harness(async(render,counts)=>{
 const denied=async()=>{throw new ApiError(401,"private detail");};profilesApi.download=denied;profilesApi.create=denied;profilesApi.rename=denied;
 await render().download(profile);await render().create(event);render().openAction("rename",profile);await render().mutate(event);
 assert.equal(counts().logouts,3);assert.equal(render().busy,"");
}));
test("validation failure unlocks draft edits without changing its key",async()=>harness(async render=>{
 profilesApi.create=async()=>{throw new ApiError(422,"ignored");};const key=render().draft.key;await render().create(event);
 assert.equal(render().draft.sent,undefined);assert.equal(render().draft.key,key);
}));
