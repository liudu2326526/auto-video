'use strict';
(async()=>{
 const config=await(await fetch('config.json',{cache:'no-store'})).json();
 const status=document.querySelector('#status'),button=document.querySelector('#check');
 const report=async detail=>{status.textContent=JSON.stringify(detail,null,2);await fetch('report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(detail)});};
 if(!navigator.mediaDevices?.getDisplayMedia){await report({state:'blocked',reason:'display_capture_unavailable',terminal:true,stream_stopped:true});return;}
 button.disabled=false;status.textContent='Ready. Metadata-only diagnostic; no exact document binding can be inferred from a title.';
 button.onclick=async()=>{
  button.disabled=true;let stream=null,expired=false,deadline;const requestedAt=performance.now();
  try{
   deadline=setTimeout(()=>{expired=true;status.textContent='Selection deadline exceeded. Cancel the browser chooser; a late grant will be stopped without reading frames.';},40000);
   if(typeof CaptureController!=='function')throw Error('focus_controller_unavailable');
   const controller=new CaptureController();controller.setFocusBehavior('focus-capturing-application');
   stream=await navigator.mediaDevices.getDisplayMedia({controller,video:{displaySurface:'browser'},audio:false,systemAudio:'exclude',monitorTypeSurfaces:'exclude',surfaceSwitching:'exclude',selfBrowserSurface:'exclude'});
   clearTimeout(deadline);
   if(expired||performance.now()-requestedAt>=40000){stream.getTracks().forEach(t=>t.stop());await report({state:'discarded',reason:'selection_deadline',frames_accessed:0,recorder_constructed:false,terminal:true,stream_stopped:true});return;}
   const tracks=stream.getVideoTracks(),track=tracks[0],settings=track?.getSettings(),handle=track?.getCaptureHandle?.();
   const audio=stream.getAudioTracks().length;
   // Stop synchronously before hashing/reporting. No pixel consumer is ever created.
   stream.getTracks().forEach(t=>t.stop());
   const stopped=stream.getTracks().every(t=>t.readyState==='ended');
   let reason=expired?'selection_deadline':settings?.displaySurface!=='browser'?'not_a_browser_tab':audio||tracks.length!==1?'unexpected_tracks':!handle?'capture_handle_not_exposed':handle.origin!==config.expected_origin?'origin_mismatch':'unrecognized_document_handle';
   const digest=handle?.handle?Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(handle.handle)))).map(x=>x.toString(16).padStart(2,'0')).join(''):null;
   await report({state:'identity_checked',reason,expected_origin:config.expected_origin,observed_origin:handle?.origin??null,handle_present:!!handle,handle_sha256:digest,display_surface:settings?.displaySurface??null,audio_tracks:audio,exact_document_bound:false,frames_accessed:0,recorder_constructed:false,terminal:true,stream_stopped:stopped});
  }catch(e){clearTimeout(deadline);stream?.getTracks().forEach(t=>t.stop());await report({state:'blocked',reason:e.name,frames_accessed:0,recorder_constructed:false,terminal:true,stream_stopped:true});}
 };
})();
