'use strict';
(()=>{let prompt;const button=document.getElementById('installApp'),status=document.getElementById('installStatus');
if('serviceWorker'in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{status.textContent='安裝支援暫不可用；本機網頁仍可正常使用。';});
window.addEventListener('beforeinstallprompt',event=>{event.preventDefault();prompt=event;button.hidden=false;});
button.addEventListener('click',async()=>{if(!prompt)return;await prompt.prompt();await prompt.userChoice;prompt=null;button.hidden=true;});
status.textContent='支援安裝的瀏覽器可將此首頁加入應用程式。本機服務須保持啟動；目前未開放手機遠端連線。';
})();
