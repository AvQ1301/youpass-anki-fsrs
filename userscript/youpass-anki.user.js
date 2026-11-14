// ==UserScript==
// @name         YouPass IELTS → Anki FSRS
// @namespace    http://127.0.0.1:8844
// @version      0.4.0
// @description  Sends new official YouPass results and normalized error evidence to the local bridge.
// @match        https://e-learning.youpass.vn/practice/*
// @grant        GM_xmlhttpRequest
// @grant        unsafeWindow
// @connect      127.0.0.1
// @run-at       document-start
// ==/UserScript==

(() => {
  "use strict";
  const endpoint="http://127.0.0.1:8844/api/results";
  const match=location.pathname.match(/^\/practice\/(reading|listening|writing-task-[12]|writing-with-ai|speaking-part-([123]))\/(?:[^/]*-)?(\d+)/);
  if(!match)return;
  const pathSkill=match[1],skill=pathSkill.startsWith("writing")?"writing":pathSkill.startsWith("speaking")?"speaking":pathSkill;
  const sourceId=match[3],exerciseId=skill==="reading"?sourceId:`${skill}:${sourceId}`;
  const submitted=new Set(JSON.parse(sessionStorage.getItem("youpass-anki-submitted")||"[]"));
  let quizPayload=null;
  const persist=()=>sessionStorage.setItem("youpass-anki-submitted",JSON.stringify([...submitted]));
  const unwrap=value=>{while(value&&typeof value==="object"&&!Array.isArray(value)&&value.data&&typeof value.data==="object"&&!Array.isArray(value.data))value=value.data;return value||{}};
  const answerId=value=>String(unwrap(value).id??"");
  const clean=value=>{if(value==null)return null;if(Array.isArray(value))return value.map(clean).filter(Boolean).join(" | ").slice(0,1000);if(typeof value==="object"){for(const key of ["answer","answers","title","text","option","value"])if(value[key]!=null)return clean(value[key]);return null}return String(value).slice(0,1000)};
  function questionMap(value){const found=new Map();function visit(node,parent){if(!node||typeof node!=="object")return;if(Array.isArray(node)){node.forEach(item=>visit(item,parent));return}if(parent==="questions"&&node.id!=null&&node.question_type!=null)found.set(String(node.id),node);Object.entries(node).forEach(([key,child])=>visit(child,key))}visit(unwrap(value).parts||unwrap(value),null);return found}
  function criterionObservations(answer){const labels={TR:"Task Response",TA:"Task Achievement",CC:"Coherence & Cohesion",LR:"Lexical Resource",GR:"Grammatical Range & Accuracy",GRA:"Grammatical Range & Accuracy",FC:"Fluency & Coherence",PR:"Pronunciation"},out=[],seen=new Set();for(const candidate of [answer.band_score_detail,answer.review_detail]){if(!candidate||typeof candidate!=="object")continue;for(const [key,label] of Object.entries(labels)){const score=Number(candidate[key]??candidate[`${key.toLowerCase()}_score`]);if(Number.isFinite(score)&&score>=0&&score<=9&&!seen.has(key)){seen.add(key);out.push({kind:"criterion_score",item_key:key,category:label,outcome:score<7?"weak":"measured",score,source:"youpass-official-result"})}}}return out}
  function observations(answer){const questions=questionMap(quizPayload),details=Array.isArray(answer.detail)?answer.detail:[],seen=new Set(),out=[];for(const detail of details){const key=String(detail.question_id??detail.id_question??"");if(!key)continue;seen.add(key);if(detail.is_correct===false||detail.is_skipped===true){const q=questions.get(key)||{};out.push({kind:"question_error",item_key:key,category:q.question_type||null,outcome:detail.is_skipped===true?"skipped":"incorrect",selected_answer:clean(detail.answer),correct_answer:clean(q.correct_answer??q.correct_answers),source:"youpass-official-result",metadata:{order:q.order??null}})}}for(const [key,q] of questions)if(!seen.has(key))out.push({kind:"question_error",item_key:key,category:q.question_type||null,outcome:"skipped",selected_answer:null,correct_answer:clean(q.correct_answer??q.correct_answers),source:"youpass-official-result",metadata:{order:q.order??null}});return [...out,...criterionObservations(answer)]}
  function findBand(value,depth=0){if(!value||typeof value!=="object"||depth>7)return null;for(const key of ["overall_band_score","band_score","overall"]){const band=Number(value[key]);if(Number.isFinite(band)&&band>=0&&band<=9)return band}for(const child of Object.values(value)){const band=findBand(child,depth+1);if(band!==null)return band}return null}
  function send(payload){const answer=unwrap(payload),id=String(answer.id??"");if(!id||!submitted.has(id))return;const body={event_id:`youpass-${skill}-answer-${id}`,exercise_id:exerciseId,source:"youpass-official-result",attempted_at:answer.date_created||null,payload:{answer_id:id,observations:observations(answer)}};if(skill==="reading"||skill==="listening"){const summary=answer.summary||{};if(!Number.isFinite(summary.correct)||!Number.isFinite(summary.total)||summary.total<=0)return;body.correct=Number(summary.correct);body.total=Number(summary.total)}else{const band=findBand(answer);if(band===null)return;body.band=band}GM_xmlhttpRequest({method:"POST",url:endpoint,headers:{"Content-Type":"application/json"},data:JSON.stringify(body),onload:r=>console.info("YouPass Anki",r.status,r.responseText),onerror:()=>console.error("YouPass Anki bridge unavailable")})}
  function inspect(url,payload,method){const text=String(url);if(/\/v1\/public\/quizzes\/\d+\//.test(text))quizPayload=payload;if(/\/v[12]\/(?:e-learning\/)?quizzes\/\d+\/(?:answer|submit)/.test(text)&&method==="POST"){const id=answerId(payload);if(id){submitted.add(id);persist();send(payload)}}if(/\/v1\/(?:public\/)?answers\/[^/?]+(?:\/status)?/.test(text))send(payload)}
  const pageWindow=typeof unsafeWindow==="undefined"?window:unsafeWindow,originalFetch=pageWindow.fetch;
  pageWindow.fetch=async function(...args){const response=await originalFetch.apply(this,args),url=String(args[0]?.url||args[0]),method=String(args[1]?.method||"GET").toUpperCase();response.clone().json().then(payload=>inspect(url,payload,method)).catch(()=>{});return response};
  const XHR=pageWindow.XMLHttpRequest,open=XHR.prototype.open,xhrSend=XHR.prototype.send;XHR.prototype.open=function(method,url,...rest){this.__ypMethod=String(method).toUpperCase();this.__ypUrl=String(url);return open.call(this,method,url,...rest)};XHR.prototype.send=function(body){this.addEventListener("load",()=>{try{inspect(this.__ypUrl,JSON.parse(this.responseText),this.__ypMethod)}catch(_){}});return xhrSend.call(this,body)};
  window.addEventListener("load",()=>{const button=document.createElement("button");button.textContent="Send result to Anki";Object.assign(button.style,{position:"fixed",right:"14px",bottom:"14px",zIndex:999999,padding:"9px 12px",border:0,borderRadius:"8px",background:"#3559d9",color:"white",cursor:"pointer"});button.onclick=()=>{const id=new URL(location.href).searchParams.get("answerId")||new URL(location.href).searchParams.get("answer_id");if(!id)return alert("No answer ID on this page.");submitted.add(String(id));persist();location.reload()};document.body.appendChild(button)});
})();
