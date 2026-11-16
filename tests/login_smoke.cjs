/* Browser-independent login regression: no libraries beyond Node's standard modules. */
'use strict';
const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const path=require('node:path');
const base=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(base,'static/index.html'),'utf8');
const script=fs.readFileSync(path.join(base,'static/login.js'),'utf8');

assert.match(html,/<form id="loginForm" method="post" action="\/api\/login"/);
assert(!html.includes('<form id="loginForm"><label>'));
assert(html.indexOf('/assets/login.js')<html.indexOf('/assets/app.js'));

async function runScenario(response){
  let handler,request,navigated, prevented=false,stopped=false, reset=false;
  const error={textContent:''},button={disabled:false};
  const form={
    elements:{namedItem(name){return {value:name==='username'?'admin':'example-not-real-password'}}},
    querySelector(){return button},
    reset(){reset=true},
    addEventListener(type,callback,capture){
      assert.equal(type,'submit');assert.equal(capture,true);handler=callback;
    }
  };
  const document={getElementById(id){return id==='loginForm'?form:id==='authError'?error:null}};
  const fetch=async(url,options)=>{request={url,options};return response};
  const window={location:{replace(url){navigated=url}}};
  vm.runInNewContext(script,{document,fetch,window});
  assert.equal(typeof handler,'function');
  await handler({preventDefault(){prevented=true},stopPropagation(){stopped=true}});
  assert(prevented&&stopped,'default GET submission must be blocked');
  assert.equal(request.url,'/api/login');
  assert.equal(request.options.method,'POST');
  assert.equal(request.options.headers['Content-Type'],'application/json');
  assert.equal(request.options.credentials,'same-origin');
  assert.equal(JSON.parse(request.options.body).username,'admin');
  assert(!request.url.includes('password='));
  assert.equal(button.disabled,false);
  return {navigated,error:error.textContent,reset};
}

(async()=>{
  const success=await runScenario({ok:true,status:200,json:async()=>({user:{role:'admin'}})});
  assert.equal(success.navigated,'/');
  assert(success.reset);
  const failure=await runScenario({ok:false,status:401,json:async()=>({detail:'Identifiants incorrects'})});
  assert.equal(failure.navigated,undefined);
  assert.equal(failure.error,'Identifiants incorrects');
  console.log('login smoke: secure POST, no GET leakage, success and error paths OK');
})().catch(ex=>{console.error(ex);process.exitCode=1});
