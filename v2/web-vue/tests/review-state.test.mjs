import {test} from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {reviewState,newRequestId} from '../src/review.js';

const original={id:'00001',sources:[],results:[],status:'missing',delivered:null,replacement:false};
const withPhoto={...original,sources:[{id:'source'}],status:'pending'};
const ready={...withPhoto,status:'review',results:[{id:'result',source_id:'source',status:'success',artifact:{id:'jpg'},review:'pending'}]};
test('business stage controls are derived from persisted records',()=>{
  let s=reviewState(original);assert(s.upload);assert(!s.process&&!s.replace&&!s.approve&&!s.deliver&&!s.undo);
  s=reviewState(withPhoto);assert(s.process&&s.upload);assert(!s.approve&&!s.replace);
  s=reviewState(ready);assert(s.approve&&s.reject&&s.process);assert(!s.undo&&!s.replace&&!s.deliver);
  const approved={...ready,status:'approved',results:[{...ready.results[0],review:'approved'}]};
  s=reviewState(approved);assert(s.undo&&s.deliver);assert(!s.upload&&!s.process&&!s.approve&&!s.reject&&!s.replace);
  s=reviewState(approved,[],[{status:'prepared'}]);assert(s.prepared);assert(!s.undo&&!s.deliver&&!s.upload&&!s.replace);
  const delivered={...approved,status:'delivered',delivered:{batch_id:'batch'}};
  s=reviewState(delivered);assert(s.replace);assert(!s.deliver&&!s.upload&&!s.process&&!s.undo);
  s=reviewState({...delivered,replacement:true,status:'review',results:ready.results});assert(s.upload&&s.process);assert(!s.replace);
  s=reviewState({...ready,status:'review_rejected',results:[{...ready.results[0],review:'rejected'}]});assert(s.undo&&s.process);assert(!s.reject&&!s.approve);
  for(const status of ['running','pausing','paused','cancelling','queued']){s=reviewState(ready,[{status}]);assert(s.lockReason);assert(!s.approve&&!s.process&&!s.upload);}
  s=reviewState(ready,[{status:'interrupted',uncertain:{'00001':'unknown'}}]);assert(s.lockReason&&!s.approve);
  s=reviewState(delivered,[],[],true);assert(s.lockReason&&!s.replace);
});
test('secure UUID native, fallback, unavailable and uniqueness',()=>{
  assert.equal(newRequestId({randomUUID:()=> 'native-id'}),'native-id');
  const fallback={getRandomValues:array=>webcrypto.getRandomValues(array)},ids=new Set();
  for(let i=0;i<100;i++){const id=newRequestId(fallback);assert.match(id,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);ids.add(id);}
  assert.equal(ids.size,100);assert.throws(()=>newRequestId({}),/安全随机数/);
});
test('current review UI removes inert checkbox without sending force-version parameter',()=>{
  const src=readFileSync(new URL('../src/components/StudentDetail.vue',import.meta.url),'utf8');
  assert(!src.includes('明确生成新处理版本'));assert(!src.includes('new_version'));
});
