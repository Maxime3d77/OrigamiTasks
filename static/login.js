/* Standalone, defensive login handling.
 * This runs independently from the main SPA: no password may enter a URL,
 * even if the dashboard code fails during its initialization.
 */
'use strict';
(() => {
  const form=document.getElementById('loginForm');
  if(!form)return;
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    event.stopPropagation();
    const error=document.getElementById('authError');
    const button=form.querySelector('button[type="submit"]');
    const username=form.elements.namedItem('username')?.value||'';
    const password=form.elements.namedItem('password')?.value||'';
    if(error)error.textContent='';
    if(button)button.disabled=true;
    try{
      const response=await fetch('/api/login',{
        method:'POST',
        credentials:'same-origin',
        cache:'no-store',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({username,password})
      });
      const result=await response.json().catch(()=>({}));
      if(!response.ok){
        throw new Error(typeof result.detail==='string'?result.detail:'Erreur de connexion (HTTP '+response.status+')');
      }
      form.reset();
      // Navigate to a clean origin URL, never carrying form fields in the query.
      window.location.replace('/');
    }catch(ex){
      if(error)error.textContent=ex.message||'Connexion impossible';
    }finally{
      if(button)button.disabled=false;
    }
  },true);
})();