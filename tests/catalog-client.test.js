import assert from 'node:assert/strict';
import test from 'node:test';
import { CatalogSession } from '../custom_components/improved_shopping_list_dev/frontend/catalog-client.js';

test('a lost save response times out without retrying or accepting a late response', async t => {
  t.mock.timers.enable({apis:['setTimeout']});
  let resolve;const calls=[];
  const session=new CatalogSession(()=>({callWS:message=>{calls.push(message);return new Promise(done=>{resolve=done;});}}),()=>{});
  const saving=session.save({id:'bread',revision:1,name:'Pan'});
  const rejected=assert.rejects(saving,/Puede haberse guardado/);
  t.mock.timers.tick(20000);await rejected;
  resolve({product:{id:'bread',revision:2,name:'Pan'}});await Promise.resolve();
  assert.equal(calls.length,1);
  assert.deepEqual(session.products,[]);
});

test('a confirmed save finishes even if the subsequent catalog refresh never responds', async t => {
  t.mock.timers.enable({apis:['setTimeout']});
  const saved={id:'bread',revision:2,name:'Pan'};let refreshStarted;
  const started=new Promise(resolve=>{refreshStarted=resolve;});
  const session=new CatalogSession(()=>({callWS:message=>{
    if(message.type.endsWith('/save'))return Promise.resolve({product:saved});
    refreshStarted();return new Promise(()=>{});
  }}),()=>{});
  const saving=session.save({id:'bread',revision:1,name:'Pan'});
  await started;t.mock.timers.tick(20000);
  assert.deepEqual(await saving,saved);
  assert.match(session.error,/no ha respondido/);
  assert.equal(session.loading,false);
});
