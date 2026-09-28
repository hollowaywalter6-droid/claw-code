"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const storageKey = "claw-desk-chat-v1", maxSaved = 60;
  let csrf = "", ownerKey = "", ready = false, busy = false, thread = [];
  let attachments = [], currentPanel = "chat", speakReplies = false, recognition = null, recorder = null;
  const fileNames = f => f.map(x => x.name).join(", ");

  function showConnection(label, kind) {
    const node = $("connection");
    node.className = "connection " + kind;
    node.replaceChildren();
    const dot = document.createElement("i");
    dot.setAttribute("aria-hidden", "true");
    node.append(dot, document.createTextNode(" " + label));
  }
  function load() {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey) || "[]");
      if (Array.isArray(saved)) thread = saved.filter(x =>
        x && ["user","assistant"].includes(x.role) && typeof x.content === "string"
      ).slice(-maxSaved).map(x => ({role:x.role,content:x.content.slice(0,12000)}));
    } catch (_) { thread = []; }
  }
  function save() {
    try { localStorage.setItem(storageKey, JSON.stringify(thread.slice(-maxSaved))); }
    catch (_) { $("notice").textContent = "Browser storage unavailable; conversations will not persist."; }
  }
  function speak(text) {
    if (!("speechSynthesis" in window)) {
      $("notice").textContent = "Voice playback is unavailable in this browser.";
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text.slice(0,5000));
    utterance.rate = 1;
    utterance.lang = navigator.language || "en-US";
    window.speechSynthesis.speak(utterance);
  }
  function addMessage(role, content) {
    const box = document.createElement("article"); box.className = "message " + role;
    const header = document.createElement("div"); header.className = "role";
    header.textContent = role === "user" ? "You" : "Claw";
    if (role === "assistant") {
      const listen = document.createElement("button");
      listen.type = "button"; listen.className = "listen-button";
      listen.textContent = "◖ Listen"; listen.setAttribute("aria-label","Read this answer aloud");
      listen.addEventListener("click", () => speak(content));
      header.append(listen);
    }
    const bubble = document.createElement("div"); bubble.className = "bubble";
    bubble.textContent = content; // never inject untrusted model output as HTML
    box.append(header,bubble); $("messages").append(box);
    $("welcome").hidden = true; $("conversation").scrollTop = $("conversation").scrollHeight;
  }
  function redraw() {
    $("messages").replaceChildren();
    $("welcome").hidden = thread.length > 0;
    for (const item of thread) addMessage(item.role,item.content);
  }
  function toggleBusy(value) {
    busy=value; $("busy").hidden=!value;
    $("send").disabled=value||!ready; $("prompt").disabled=value||!ready;
    $("mode").disabled=value; $("attach-button").disabled=value||!ready;
    $("voice-button").disabled=value||!ready;
    $("kill-switch").disabled=!ownerKey;
  }
  async function request(path,options={}) {
    const headers={...(options.headers||{})};
    if (path !== "/api/bootstrap") {
      headers["X-Claw-Desk-Token"]=csrf;
      headers["X-Claw-Desk-Owner-Key"]=ownerKey;
    }
    const response=await fetch(path,{cache:"no-store",credentials:"same-origin",...options,headers});
    const data=await response.json().catch(()=>({}));
    if (!response.ok) throw Error(typeof data.error==="string"?data.error:"Request failed ("+response.status+")");
    return data;
  }
  async function bootstrap() {
    try {
      const data=await request("/api/bootstrap");
      csrf=data.csrf; ready=false; ownerKey="";
      $("unlock-gate").hidden=false; $("unlock-error").textContent="";
      showConnection("Locked","error"); $("send").disabled=true;
      $("kill-switch").disabled=true; $("prompt").disabled=true;
      $("notice").textContent="Enter your private host owner key.";
      $("owner-key").focus();
    } catch (_) {
      ready=false; ownerKey=""; $("unlock-gate").hidden=true;
      showConnection("Offline","error");
      $("notice").textContent="Host unavailable. Check your private Tailscale connection.";
      $("send").disabled=true; $("kill-switch").disabled=true; $("prompt").disabled=true;
    }
  }
  async function unlock(event) {
    event.preventDefault();
    if (!csrf||$("owner-key").value.length<24) return;
    ownerKey=$("owner-key").value; $("unlock-button").disabled=true;
    $("unlock-error").textContent="";
    try {
      const data=await request("/api/unlock",{method:"POST"});
      if (!data.authorized) throw Error("Owner key not accepted.");
      $("owner-key").value=""; $("unlock-gate").hidden=true;
      ready=Boolean(data.ready);
      showConnection(ready?"Connected":"CLI missing",ready?"ready":"error");
      $("notice").textContent=ready?"Private Claw host connected.":"Build the Rust Claw CLI on your host to enable chat.";
      $("send").disabled=!ready; $("prompt").disabled=!ready;
      $("attach-button").disabled=!ready; $("voice-button").disabled=!ready;
      $("kill-switch").disabled=false;
      if (ready) $("prompt").focus();
    } catch (error) { ownerKey=""; $("unlock-error").textContent=error.message; }
    finally { $("unlock-button").disabled=false; }
  }
  function stopLocalMedia() {
    if (recognition) {
      recognition.onresult=null;
      recognition.onerror=null;
      recognition.onend=null;
      try { recognition.abort(); } catch (_) {}
      recognition=null;
    }
    if (recorder) {
      const active=recorder;
      recorder=null;
      active.onstop=null; // shutdown must not trigger a delayed upload
      try { if (active.state!=="inactive") active.stop(); } catch (_) {}
      try { active.stream.getTracks().forEach(track=>track.stop()); } catch (_) {}
    }
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    $("voice-button").textContent="🎙 Speak";
  }
  async function shutdown() {
    if (!ownerKey||!csrf) return;
    if (!window.confirm("EMERGENCY STOP\n\nShut down Claw Desk and cancel active Claw tasks now? You must restart the trusted host to reconnect.")) return;
    stopLocalMedia();
    $("kill-switch").disabled=true;
    try {
      const result=await request("/api/shutdown",{method:"POST"});
      if (!result.stopped) throw Error("Shutdown was not confirmed.");
      ownerKey=""; csrf=""; ready=false;
      $("unlock-gate").hidden=true; $("send").disabled=true;
      $("prompt").disabled=true; $("mode").disabled=true; $("busy").hidden=true;
      showConnection("Shut down","error");
      $("notice").textContent="OWNER SHUTDOWN COMPLETE. Restart the trusted host to reconnect.";
      if (currentPanel!=="chat") selectPanel("chat");
      addMessage("assistant","Owner emergency shutdown accepted. The server has stopped and tracked Claw jobs were cancelled.");
    } catch (error) {
      $("notice").textContent="Shutdown not confirmed: "+error.message;
      $("kill-switch").disabled=false;
    }
  }
  function historyForRequest() {
    return thread.slice(-8).map(x=>({role:x.role,content:x.content.slice(0,2000)}));
  }
  function drawUploads() {
    const tray=$("upload-tray"); tray.replaceChildren();
    tray.hidden=attachments.length===0;
    for (const attachment of attachments) {
      const chip=document.createElement("span"); chip.className="upload-chip";
      chip.append(document.createTextNode("📎 "+attachment.name+" "));
      const remove=document.createElement("button"); remove.type="button";
      remove.textContent="×"; remove.setAttribute("aria-label","Remove "+attachment.name);
      remove.addEventListener("click",()=>{
        attachments=attachments.filter(a=>a.id!==attachment.id); drawUploads();
      });
      chip.append(remove); tray.append(chip);
    }
  }
  function blobBase64(blob) {
    return new Promise((resolve,reject)=>{
      const reader=new FileReader();
      reader.onload=()=>resolve(String(reader.result).split(",")[1]||"");
      reader.onerror=()=>reject(Error("Unable to read this file."));
      reader.readAsDataURL(blob);
    });
  }
  async function normalizePhonePhoto(file) {
    const direct=["image/png","image/jpeg","image/webp"];
    if (direct.includes(file.type)&&file.size<=6000000) return file;
    if (!file.type.startsWith("image/")&&!/\.(heic|heif|jpg|jpeg|png|webp)$/i.test(file.name)) return file;
    // Safari can provide iPhone photos in HEIC. Convert to a bounded JPEG
    // on the device, so no unsupported HEIC bytes reach the AI provider.
    let picture, objectURL;
    try {
      if ("createImageBitmap" in window) {
        try { picture=await createImageBitmap(file); } catch (_) {}
      }
      if (!picture) {
        objectURL=URL.createObjectURL(file);
        picture=await new Promise((resolve,reject)=>{
          const image=new Image();
          image.onload=()=>resolve(image);
          image.onerror=()=>reject(Error("Unable to decode this photo. Try JPEG or PNG."));
          image.src=objectURL;
        });
      }
      const width=picture.width||picture.naturalWidth;
      const height=picture.height||picture.naturalHeight;
      if (!width||!height) throw Error("Invalid photo dimensions.");
      const scale=Math.min(1,2048/Math.max(width,height));
      const canvas=document.createElement("canvas");
      canvas.width=Math.max(1,Math.round(width*scale));
      canvas.height=Math.max(1,Math.round(height*scale));
      const context=canvas.getContext("2d");
      if (!context) throw Error("Photo conversion unavailable.");
      context.drawImage(picture,0,0,canvas.width,canvas.height);
      const converted=await new Promise(resolve=>canvas.toBlob(resolve,"image/jpeg",0.82));
      if (!converted) throw Error("Photo conversion failed.");
      return new File([converted],file.name.replace(/\.[^.]+$/,"")+".jpg",{type:"image/jpeg"});
    } finally {
      if (picture&&typeof picture.close==="function") picture.close();
      if (objectURL) URL.revokeObjectURL(objectURL);
    }
  }
  async function uploadFiles(event) {
    const items=[...(event.target.files||[])]; event.target.value="";
    if (items.length+attachments.length>3) {
      $("notice").textContent="Select at most three attachments per message."; return;
    }
    if (!ready||busy) return;
    $("attach-button").disabled=true;
    try {
      for (const original of items) {
        const file=await normalizePhonePhoto(original);
        if (file.size>6000000||!file.size) throw Error("Each attachment must be between 1 byte and 6 MB after photo optimization.");
        let mime=file.type;
        if (!mime&&file.name.toLowerCase().endsWith(".pdf")) mime="application/pdf";
        if (!mime&&file.name.toLowerCase().endsWith(".txt")) mime="text/plain";
        const data=await blobBase64(file);
        const response=await request("/api/files/upload",{
          method:"POST",headers:{"Content-Type":"application/json"},
          body:JSON.stringify({name:file.name,media_type:mime,data})
        });
        attachments.push(response);
      }
      drawUploads();
      $("notice").textContent="Files uploaded privately for this message. Multimodal prompts use the host's Anthropic key (no Claw file-edit tools).";
    } catch (error) { $("notice").textContent="Attachment error: "+error.message; }
    finally { $("attach-button").disabled=!ready; }
  }
  async function send(event) {
    event.preventDefault();
    let text=$("prompt").value.trim();
    if (!text&&attachments.length) text="Please analyze the attached material.";
    if (!ready||busy||!text||text.length>6000) return;
    const previous=historyForRequest(), mode=$("mode").value;
    const pending=[...attachments];
    $("prompt").value=""; $("prompt").style.height="";
    const visible=text+(pending.length?"\n\n📎 "+fileNames(pending):"");
    addMessage("user",visible); toggleBusy(true);
    $("input-hint").textContent="Working on your request…";
    try {
      const result=await request("/api/chat",{
        method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({message:text,mode,history:previous,
          attachments:pending.map(x=>x.id)})
      });
      const reply=typeof result.message==="string"&&result.message
        ?result.message:"(No readable answer was returned.)";
      addMessage("assistant",reply);
      thread.push({role:"user",content:visible},{role:"assistant",content:reply});
      thread=thread.slice(-maxSaved); save();
      attachments=[]; drawUploads();
      $("notice").textContent=pending.length
        ?"Attachment analysis completed using your configured multimodal provider (without Claw tools)."
        :mode==="workspace-write"?"Workspace-write request completed.":"Read-only request completed.";
      if (speakReplies) speak(reply);
    } catch (error) {
      addMessage("assistant","Unable to complete this message: "+error.message+"\n\nYour draft was restored.");
      $("prompt").value=text;
      $("notice").textContent="Request unsuccessful. Check the host or diagnostics.";
      if (/fetch|network/i.test(error.message)) {
        showConnection("Connection lost","error"); ready=false;
      }
    } finally {
      toggleBusy(false); $("send").disabled=!ready; $("prompt").disabled=!ready;
      $("input-hint").textContent="Tap ↑ to send · images/PDFs use private multimodal mode";
      $("conversation").scrollTop=$("conversation").scrollHeight;
    }
  }
  async function recordVoice() {
    if (!ready||busy) return;
    if (recognition) { recognition.stop(); recognition=null; $("voice-button").textContent="🎙 Speak"; return; }
    if (recorder&&recorder.state==="recording") {
      recorder.stop(); $("voice-button").textContent="🎙 Speak"; return;
    }
    const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
    if (SR) {
      recognition=new SR(); recognition.lang=navigator.language||"en-US";
      recognition.interimResults=false; recognition.continuous=false;
      recognition.onresult=e=>{
        const words=Array.from(e.results).map(r=>r[0].transcript).join(" ");
        $("prompt").value=($("prompt").value.trim()+" "+words).trim();
        $("prompt").dispatchEvent(new Event("input"));
      };
      recognition.onerror=e=>{$("notice").textContent="Speech input: "+e.error;};
      recognition.onend=()=>{recognition=null; $("voice-button").textContent="🎙 Speak";};
      recognition.start(); $("voice-button").textContent="■ Stop";
      $("notice").textContent="Listening… tap Stop or finish speaking.";
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder) {
      $("notice").textContent="Voice recording is not available in this browser."; return;
    }
    try {
      const stream=await navigator.mediaDevices.getUserMedia({audio:true});
      const types=["audio/mp4","audio/webm","audio/wav"];
      const chosen=types.find(x=>MediaRecorder.isTypeSupported(x));
      recorder=chosen?new MediaRecorder(stream,{mimeType:chosen}):new MediaRecorder(stream);
      const chunks=[];
      recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
      recorder.onstop=async()=>{
        stream.getTracks().forEach(x=>x.stop());
        $("voice-button").textContent="🎙 Speak";
        const blob=new Blob(chunks,{type:recorder.mimeType||chosen||"audio/mp4"});
        recorder=null;
        if (blob.size>8000000) { $("notice").textContent="Audio exceeded the 8 MB limit."; return; }
        try {
          $("notice").textContent="Transcribing recorded voice…";
          const response=await request("/api/voice/transcribe",{
            method:"POST",headers:{"Content-Type":"application/json"},
            body:JSON.stringify({name:"voice."+(/webm/.test(blob.type)?"webm":"m4a"),
              media_type:blob.type,data:await blobBase64(blob)})
          });
          $("prompt").value=($("prompt").value.trim()+" "+response.text).trim();
          $("prompt").dispatchEvent(new Event("input"));
          $("notice").textContent="Voice transcribed. Review and tap ↑ to send.";
        } catch(error) { $("notice").textContent="Transcription failed: "+error.message; }
      };
      recorder.start(); $("voice-button").textContent="■ Stop";
      $("notice").textContent="Recording… tap Stop. Audio transcription uses your host OPENAI_API_KEY.";
    } catch(error) { $("notice").textContent="Microphone unavailable: "+error.message; }
  }
  async function inspect(path,label) {
    $("diagnostics").hidden=false; $("diagnostics-label").textContent=label;
    $("diagnostics-output").textContent="Loading…";
    try {
      const result=await request(path);
      $("diagnostics-output").textContent=typeof result.message==="string"
        ?result.message+(result.data?"\n\n"+JSON.stringify(result.data,null,2):"")
        :JSON.stringify(result,null,2);
    } catch(error) {$("diagnostics-output").textContent=error.message;}
    selectPanel("chat"); $("conversation").scrollTop=$("conversation").scrollHeight;
  }
  function clearChat() {
    if (!thread.length) return;
    if (!window.confirm("Clear this conversation from your iPhone? This cannot be undone.")) return;
    thread=[]; save(); $("messages").replaceChildren(); $("welcome").hidden=false;
    $("diagnostics").hidden=true; $("notice").textContent="Conversation cleared.";
  }
  function selectPanel(name) {
    currentPanel=name;
    $("feature-panel").hidden=name==="chat";
    $("welcome").hidden=name!=="chat"||thread.length>0;
    $("messages").hidden=name!=="chat";
    $("busy").hidden=name!=="chat"||!busy;
    $("diagnostics").hidden=name!=="chat"||$("diagnostics").hidden;
    $("composer-wrap").hidden=name!=="chat";
    for (const panel of ("agents","integrations","tasks")) {
      $("panel-"+panel).hidden=panel!==name;
      $("nav-"+panel).classList.toggle("active",panel===name);
    }
    $("nav-chat").classList.toggle("active",name==="chat");
    $("mobile-tools").hidden=true; $("mobile-menu").setAttribute("aria-expanded","false");
    $("conversation").scrollTop=0;
    if (name==="integrations") refreshGoogle();
    if (name==="tasks") loadTasks();
  }
  function result(id,text) { $(id).textContent=text; }
  async function refreshGoogle() {
    if (!ownerKey) return;
    try {
      const status=await request("/api/google/status");
      result("google-status",status.connected?"● Connected to Google"
        :status.configured?"○ OAuth configured; tap Connect Google.":"○ Host Google OAuth not configured. See setup guide.");
    } catch(error){result("google-status",error.message);}
  }
  async function connectGoogle() {
    const popup=window.open("about:blank","_blank");
    try {
      const response=await request("/api/google/connect",{method:"POST"});
      if (popup) popup.location.href=response.url;
      else window.location.assign(response.url);
      result("google-status","Follow Google's consent screen, return here and tap Refresh connection.");
    } catch(error) {
      if (popup) popup.close();
      result("google-status",error.message);
    }
  }
  function renderDrive(data) {
    const box=$("drive-results"); box.replaceChildren();
    if (!data.files?.length) {box.textContent="No matching Drive files.";return;}
    for (const file of data.files) {
      const line=document.createElement("div"); line.className="resource-line";
      const name=document.createElement("span");
      name.textContent=(file.name||"Unnamed")+" · "+(file.mimeType||"")+" · "+(file.modifiedTime||"");
      line.append(name);
      try {
        const url=new URL(file.webViewLink||"");
        if (url.protocol==="https:"&&(url.hostname==="google.com"||url.hostname.endsWith(".google.com"))) {
          const anchor=document.createElement("a"); anchor.href=url.toString();
          anchor.target="_blank"; anchor.rel="noopener noreferrer"; anchor.textContent="Open ↗";
          line.append(anchor);
        }
      } catch(_) {}
      box.append(line);
    }
  }
  async function driveSearch(event) {
    event.preventDefault();result("drive-results","Searching…");
    try {
      const data=await request("/api/google/drive?q="+encodeURIComponent($("drive-query").value.trim()));
      renderDrive(data);
    } catch(error){result("drive-results",error.message);}
  }
  async function readGoogle(kind) {
    result("google-results","Loading…");
    try {
      const data=await request("/api/google/"+kind);
      if (kind==="mail") result("google-results",data.messages?.length
        ?data.messages.map(m=>"SUBJECT: "+m.subject+"\nFROM: "+m.from+"\n"+m.snippet).join("\n\n────────\n\n")
        :"No recent inbox messages.");
      else result("google-results",data.events?.length
        ?data.events.map(e=>e.summary+"\n"+(e.start?.dateTime||e.start?.date||"")
          +"\n"+(e.description||"")).join("\n\n────────\n\n")
        :"No upcoming calendar events.");
    } catch(error){result("google-results",error.message);}
  }
  async function makeDraft(event) {
    event.preventDefault();
    const to=$("draft-to").value.trim(),subject=$("draft-subject").value.trim(),body=$("draft-body").value;
    if (!window.confirm("Create an unsent Gmail draft to "+to+" with subject “"+subject+"”? No email will be sent.")) return;
    try {
      const response=await request("/api/google/draft",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({to,subject,body,confirm:true})});
      result("google-status","Draft "+response.id+" created. It was NOT sent. Review in Gmail before sending.");
      $("draft-form").reset();
    } catch(error){result("google-status","Draft failed: "+error.message);}
  }
  async function makeEvent(event) {
    event.preventDefault();
    const summary=$("event-title").value.trim();
    const start=new Date($("event-start").value),end=new Date($("event-end").value);
    if (!Number.isFinite(start.getTime())||!Number.isFinite(end.getTime())||end<=start) {
      result("google-status","Enter a valid start and end time.");return;
    }
    if (!window.confirm("Create calendar event “"+summary+"” from "+start.toLocaleString()+" to "+end.toLocaleString()+"?")) return;
    try {
      const response=await request("/api/google/event",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({summary,start:start.toISOString(),end:end.toISOString(),confirm:true})});
      result("google-status","Calendar event created: "+response.id);
      $("event-form").reset();
    } catch(error){result("google-status","Calendar event failed: "+error.message);}
  }
  let agentTimer=null;
  async function startAgents(event) {
    event.preventDefault();
    const prompt=$("agent-task").value.trim(),mode=$("agent-mode").value;
    const confirmWrite=$("agent-confirm").checked;
    if (mode==="workspace-write"&&!confirmWrite) {
      result("agent-result","Tick the explicit workspace-write authorization checkbox first.");return;
    }
    if (mode==="workspace-write"&&!window.confirm("Allow the Executor role to modify files in the trusted Claw workspace? Architect and Reviewer remain read-only.")) return;
    $("agent-launch").disabled=true; result("agent-result","Starting Architect → Executor → Reviewer…");
    try {
      const data=await request("/api/agents/start",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({prompt,mode,confirm_write:confirmWrite})});
      if (agentTimer) clearTimeout(agentTimer);
      const poll=async()=>{
        try {
          const job=await request("/api/agents/job?id="+encodeURIComponent(data.id));
          result("agent-result","STATE: "+job.state+"\nPHASE: "+job.phase+"\n\n"
            +(job.steps||[]).map(s=>"══ "+s.role.toUpperCase()+" ["+s.mode+"] ══\n"+s.output).join("\n\n"));
          if (job.state==="running") agentTimer=setTimeout(poll,1800);
          else $("agent-launch").disabled=false;
        } catch(error){
          result("agent-result",error.message); $("agent-launch").disabled=false;
        }
      };
      poll();
    } catch(error){result("agent-result",error.message);$("agent-launch").disabled=false;}
  }
  async function loadTasks() {
    if (!ownerKey) return;
    try {
      const data=await request("/api/tasks");
      const box=$("task-results");box.replaceChildren();
      if (!data.tasks?.length) {box.textContent="No scheduled tasks yet.";return;}
      for (const task of data.tasks) {
        const card=document.createElement("div");card.className="task-card";
        const heading=document.createElement("strong");heading.textContent=task.kind.replaceAll("_"," ")+" · "+task.state;
        const timing=document.createElement("small");
        timing.textContent="Next: "+(task.next_at?new Date(task.next_at*1000).toLocaleString():"completed")
          +" · "+({0:"once",3600:"hourly",86400:"daily",604800:"weekly"}[task.interval_seconds]||"");
        const instruction=document.createElement("p"); instruction.textContent=task.prompt||"(default briefing)";
        const resultNode=document.createElement("pre");resultNode.textContent=task.result||"No result yet.";
        const remove=document.createElement("button");remove.type="button";
        remove.textContent="Delete task";remove.className="secondary-action";
        remove.addEventListener("click",async()=>{
          if (!window.confirm("Delete this scheduled task?")) return;
          try {
            await request("/api/tasks/delete",{method:"POST",headers:{"Content-Type":"application/json"},
              body:JSON.stringify({id:task.id})});
            loadTasks();
          } catch(error){$("notice").textContent=error.message;}
        });
        card.append(heading,timing,instruction,resultNode,remove);box.append(card);
      }
    } catch(error){result("task-results",error.message);}
  }
  async function createTask(event) {
    event.preventDefault();
    const date=new Date($("task-date").value);
    if (!Number.isFinite(date.getTime())) {result("task-results","Select a valid future date.");return;}
    const kind=$("task-kind").value,prompt=$("task-prompt").value,
      interval_seconds=Number($("task-repeat").value);
    const providerDisclosure=kind==="prompt"?"":" Gmail snippets/calendar event metadata retrieved for this digest will be sent to your configured AI provider.";
    if (!window.confirm("Schedule this read-only "+kind.replaceAll("_"," ")+" job? It will run on your trusted host when online."+providerDisclosure)) return;
    try {
      await request("/api/tasks/create",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({kind,prompt,run_at:date.toISOString(),interval_seconds})});
      $("task-form").reset();loadTasks();
    } catch(error){result("task-results",error.message);}
  }
  function menu(open) {
    $("mobile-tools").hidden=!open;
    $("mobile-menu").setAttribute("aria-expanded",String(open));
  }

  $("unlock-form").addEventListener("submit",unlock);
  $("kill-switch").addEventListener("click",shutdown);
  $("chat-form").addEventListener("submit",send);
  $("file-picker").addEventListener("change",uploadFiles);
  $("attach-button").addEventListener("click",()=>$("file-picker").click());
  $("voice-button").addEventListener("click",recordVoice);
  $("tts-button").addEventListener("click",()=>{
    speakReplies=!speakReplies;
    $("tts-button").textContent=speakReplies?"◖ Voice reply: on":"◖ Voice reply: off";
    $("tts-button").setAttribute("aria-pressed",String(speakReplies));
    if (speakReplies) speak("Voice replies enabled.");
    else if ("speechSynthesis" in window) speechSynthesis.cancel();
  });
  $("prompt").addEventListener("keydown",event=>{
    if (event.key==="Enter"&&!event.shiftKey&&!event.isComposing&&event.keyCode!==229
      &&!/iPhone|iPad|iPod/.test(navigator.userAgent)){
      event.preventDefault();$("chat-form").requestSubmit();
    }
  });
  $("prompt").addEventListener("input",function(){
    this.style.height="auto";this.style.height=Math.min(this.scrollHeight,160)+"px";
  });
  $("mode").addEventListener("change",event=>{
    if (event.target.value==="workspace-write"&&!window.confirm(
      "Enable workspace-write for upcoming messages? Claw may edit files inside the trusted host repository.")){
      event.target.value="read-only";
    }
  });
  for (const btn of document.querySelectorAll(".suggestion")) btn.addEventListener("click",()=>{
    selectPanel("chat");$("prompt").value=btn.getAttribute("data-prompt");
    $("prompt").dispatchEvent(new Event("input"));$("prompt").focus();
  });
  $("check-status").addEventListener("click",()=>inspect("/api/status","System status"));
  $("run-doctor").addEventListener("click",()=>inspect("/api/doctor","Health check"));
  $("status-mobile").addEventListener("click",()=>inspect("/api/status","System status"));
  $("doctor-mobile").addEventListener("click",()=>inspect("/api/doctor","Health check"));
  $("clear-chat-side").addEventListener("click",clearChat);
  $("clear-chat-mobile").addEventListener("click",()=>{menu(false);clearChat();});
  $("close-diagnostics").addEventListener("click",()=>{$("diagnostics").hidden=true;});
  $("nav-chat").addEventListener("click",()=>selectPanel("chat"));
  for (const name of ("agents","integrations","tasks")) $("nav-"+name).addEventListener("click",()=>selectPanel(name));
  for (const btn of document.querySelectorAll("#mobile-tools [data-panel]"))
    btn.addEventListener("click",()=>selectPanel(btn.getAttribute("data-panel")));
  $("mobile-menu").addEventListener("click",()=>menu($("mobile-tools").hidden));
  $("agent-form").addEventListener("submit",startAgents);
  $("google-connect").addEventListener("click",connectGoogle);
  $("google-refresh").addEventListener("click",refreshGoogle);
  $("drive-form").addEventListener("submit",driveSearch);
  $("google-mail").addEventListener("click",()=>readGoogle("mail"));
  $("google-calendar").addEventListener("click",()=>readGoogle("calendar"));
  $("draft-form").addEventListener("submit",makeDraft);
  $("event-form").addEventListener("submit",makeEvent);
  $("task-form").addEventListener("submit",createTask);
  $("refresh-tasks").addEventListener("click",loadTasks);
  window.addEventListener("online",()=>{if (!ownerKey) bootstrap();});
  if ("serviceWorker" in navigator && (location.protocol==="https:"||
    location.hostname==="localhost"||location.hostname==="127.0.0.1")){
    navigator.serviceWorker.register("/sw.js").catch(()=>{});
  }
  load();redraw();bootstrap();
})();