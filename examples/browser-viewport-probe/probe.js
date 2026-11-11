/* Supervised probe, not a generic browser recording backend. No evaluate injection. */
'use strict';
(async () => {
  const status = document.querySelector('#status');
  const config = await (await fetch('config.json', {cache:'no-store'})).json();
  const channel = new BroadcastChannel(config.handle);
  if (document.body.dataset.page === 'fixture') {
    if (!navigator.mediaDevices?.setCaptureHandleConfig) { status.textContent='Capture Handle unavailable'; return; }
    navigator.mediaDevices.setCaptureHandleConfig({handle:config.handle, exposeOrigin:true, permittedOrigins:[config.origin]});
    status.textContent='Ready: exact fixture identity exposed to this local recorder only';
    let started = performance.now();
    function animate(now) {
      const elapsed = now-started;
      document.querySelector('#clock').textContent=`FRAME CLOCK ${elapsed.toFixed(0)} ms`;
      document.querySelector('#phase').textContent=`Phase ${Math.floor(elapsed/1000)%6}`;
      requestAnimationFrame(animate);
    }
    requestAnimationFrame(animate);
    document.querySelector('#mutate').onclick=()=>document.body.classList.toggle('changed');
    document.querySelector('#invalidate').onclick=()=>navigator.mediaDevices.setCaptureHandleConfig({handle:'invalidated',exposeOrigin:true,permittedOrigins:[config.origin]});
    document.addEventListener('pointermove',e=>document.querySelector('#pointer').textContent=`Pointer ${e.clientX}, ${e.clientY}`);
    channel.onmessage=e=>{if(e.data==='start'){
      started=performance.now();
      setTimeout(()=>document.body.classList.add('changed'),2000);
      setTimeout(()=>document.body.classList.remove('changed'),4000);
      if(config.test_case==='invalidate')setTimeout(()=>navigator.mediaDevices.setCaptureHandleConfig({handle:'invalidated',exposeOrigin:true,permittedOrigins:[config.origin]}),700);
    }};
    return;
  }
  let stream=null, recorder=null, chunks=[], bytes=0, failed=null, expectedEnd=false, done=false;
  let durationTimer=null, watch=null, promptTimer=null, started=0, initial=null, requestedAt=null, pickerResolved=false;
  let renderedFrames=0;
  const preview=document.querySelector('#preview');
  const start=document.querySelector('#start'), cancel=document.querySelector('#cancel');
  const events=[];
  const event=(name,detail={})=>{events.push({name,elapsed_ms:started?performance.now()-started:0,request_elapsed_ms:requestedAt===null?null:performance.now()-requestedAt,...detail});};
  async function report(state,detail={}) {
    status.textContent=JSON.stringify({state,...detail},null,2);
    await fetch('report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({state,...detail,events,user_agent:navigator.userAgent})});
  }
  function stopTracks(){stream?.getTracks().forEach(t=>t.stop());preview.srcObject=null;}
  function identity(track) {
    const s=track.getSettings(), h=track.getCaptureHandle?.();
    if(s.displaySurface!=='browser')throw Error('not_a_browser_tab');
    if(stream.getVideoTracks().length!==1||stream.getAudioTracks().length!==0)throw Error('unexpected_tracks');
    if(!h||h.origin!==config.origin||h.handle!==config.handle)throw Error('fixture_identity_missing_or_changed');
    if(!Number.isFinite(s.width)||!Number.isFinite(s.height)||s.width<2||s.height<2||s.width*s.height>4194304)throw Error('invalid_or_overbudget_size');
    if(initial&&(s.width!==initial.width||s.height!==initial.height)){event('dimension_mismatch',{expected:[initial.width,initial.height],actual:[s.width,s.height]});throw Error('dimensions_changed_discard');}
    if(track.readyState!=='live'||track.muted)throw Error('source_not_live');
    return s;
  }
  function abort(reason) {
    if(done||failed)return;
    failed=reason; event('abort',{reason,discarded_chunks:chunks.length,discarded_bytes:bytes});chunks=[]; bytes=0;
    clearTimeout(durationTimer); clearTimeout(promptTimer); clearInterval(watch);
    const recording=recorder?.state==='recording';
    if(recording)recorder.stop();
    stopTracks(); cancel.disabled=true;
    if(!recording){done=true;report('discarded',{reason,terminal:pickerResolved,stream_stopped:true,selection_pending:!pickerResolved});}
  }
  cancel.onclick=()=>abort('explicit_cancel');
  addEventListener('pagehide',()=>{failed='page_closed';chunks=[];stopTracks();});
  if(!navigator.mediaDevices?.getDisplayMedia||!window.MediaRecorder||!('getCaptureHandle' in MediaStreamTrack.prototype)) {
    await report('blocked',{reason:'required_browser_api_unavailable',terminal:true,stream_stopped:true});return;
  }
  await report('ready',{secure_context:isSecureContext,fixture_handle_required:true}); start.disabled=false;
  start.onclick=async()=>{
    start.disabled=true;cancel.disabled=false;requestedAt=performance.now();event('prompt_requested');
    // A late grant cannot begin recording after this bounded selection period.
    promptTimer=setTimeout(()=>abort('selection_deadline'),config.selection_timeout_ms);
    try {
      if(typeof CaptureController!=='function')throw Error('focus_controller_unavailable');
      const controller=new CaptureController();
      // Keep the recorder active during acquisition so background timer clamping
      // cannot prevent bounded size checks. This changes focus, not security UI.
      controller.setFocusBehavior('focus-capturing-application');
      stream=await navigator.mediaDevices.getDisplayMedia({controller,video:{displaySurface:'browser',frameRate:{ideal:30,max:30},width:{ideal:1280,max:1920},height:{ideal:720,max:1080}},audio:false,systemAudio:'exclude',monitorTypeSurfaces:'exclude',surfaceSwitching:'exclude',selfBrowserSurface:'exclude'});
      pickerResolved=true;
      clearTimeout(promptTimer);
      if(!failed&&performance.now()-requestedAt>=config.selection_timeout_ms){failed='selection_deadline';done=true;event('deadline_on_resolution');}
      if(failed){stopTracks();event('late_grant_stopped');await report('discarded',{reason:failed,late_grant_stopped:true,bytes_retained:0,terminal:true,stream_stopped:true});return;}
      const track=stream.getVideoTracks()[0];
      identity(track);
      preview.srcObject=stream;
      let playDeadline;
      try{await Promise.race([preview.play(),new Promise((_,reject)=>{playDeadline=setTimeout(()=>reject(Error('preview_deadline')),1500);})]);}
      finally{clearTimeout(playDeadline);}
      // Selection/focus can initially report a provisional track size. No encoder
      // or output exists while waiting for five matching observations (bounded 2s).
      let stable=0, previous='', settled=false;
      const acquisition=performance.now();
      while(performance.now()-acquisition<2000&&!failed){
        const s=identity(track), key=`${s.width}x${s.height}/${preview.videoWidth}x${preview.videoHeight}`;
        event('acquisition_sample',{t_ms:performance.now()-acquisition,width:s.width,height:s.height,preview_width:preview.videoWidth,preview_height:preview.videoHeight,visible:document.visibilityState});
        stable=key===previous?stable+1:0;previous=key;
        if(stable>=5&&s.width===preview.videoWidth&&s.height===preview.videoHeight){initial=s;settled=true;break;}
        await new Promise(resolve=>setTimeout(resolve,100));
      }
      if(failed){stopTracks();return;}
      if(!settled)throw Error('source_size_did_not_settle');
      event('source_settled',{settings:initial});
      if(typeof preview.requestVideoFrameCallback!=='function')throw Error('frame_callback_unavailable');
      const observeFrame=(_,meta)=>{
        if(failed||done)return;
        try{
          identity(track);
          if(meta.width!==initial.width||meta.height!==initial.height)throw Error('frame_dimensions_changed_discard');
          renderedFrames++;
          preview.requestVideoFrameCallback(observeFrame);
        }catch(e){abort(e.message);}
      };
      preview.requestVideoFrameCallback(observeFrame);
      const mime=['video/webm;codecs=vp9','video/webm;codecs=vp8'].find(x=>MediaRecorder.isTypeSupported(x));
      if(!mime)throw Error('webm_encoder_unavailable');
      track.addEventListener('capturehandlechange',()=>{try{identity(track);}catch(e){abort(e.message);}});
      track.addEventListener('ended',()=>{if(!expectedEnd)abort('source_ended');});
      track.addEventListener('mute',()=>abort('source_muted'));
      recorder=new MediaRecorder(stream,{mimeType:mime,videoBitsPerSecond:4000000});
      recorder.ondataavailable=e=>{
        if(failed)return;
        if(bytes+e.data.size>config.limit_bytes){abort('encoded_byte_budget');return;}
        bytes+=e.data.size;chunks.push(e.data);
      };
      recorder.onerror=()=>abort('encoder_error');
      recorder.onstop=async()=>{
        clearInterval(watch);clearTimeout(durationTimer);done=true;cancel.disabled=true;
        try {
          if(failed)throw Error(failed);
          identity(track);
          if(!expectedEnd||performance.now()-started>config.duration_ms+750||bytes===0)throw Error('incomplete_or_late_stop');
          const blob=new Blob(chunks,{type:'video/webm'}); chunks=[];
          stopTracks();
          const response=await fetch('video',{method:'POST',headers:{'Content-Type':'video/webm'},body:blob});
          if(!response.ok)throw Error('local_sink_rejected');
          event('saved'); await report('saved',{mime,bytes,settings:initial,elapsed_ms:performance.now()-started,audio_tracks:0,rendered_frames:renderedFrames,terminal:true,stream_stopped:true});
        } catch(e){chunks=[];stopTracks();await report('discarded',{reason:e.message,bytes_retained:0,rendered_frames:renderedFrames,terminal:true,stream_stopped:true});}
      };
      recorder.onstart=()=>{
        if(failed||done){stopTracks();return;}
        started=performance.now();event('recording_started');channel.postMessage('start');
        report('recording',{settings:initial,mime});
        watch=setInterval(()=>{try{identity(track);}catch(e){abort(e.message);}},50);
        durationTimer=setTimeout(()=>{try{identity(track);expectedEnd=true;event('stop_intent');recorder.stop();}catch(e){abort(e.message);}},config.duration_ms);
      };
      recorder.start(250);
    } catch(e){pickerResolved=true;clearTimeout(promptTimer);if(failed){stopTracks();await report('discarded',{reason:failed,selection_resolved:true,terminal:true,stream_stopped:true});}else abort(e.name+': '+e.message);}
  };
})();
