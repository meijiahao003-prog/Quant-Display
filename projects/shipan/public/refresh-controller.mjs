import { shanghaiDate } from './market-time.mjs';

/** Refresh the visible dashboard every minute and immediately after a date change
 * or resume. Injectable clocks/hooks let tests exercise a real Shanghai midnight
 * without waiting. Inputs, calls and observed test output: docs/DAILY_REFRESH.md. */
export function createRefreshController({refresh, now=Date.now, isVisible=()=>true,
  isActive=()=>true, isBusy=()=>false, onDateChange=()=>{}, interval=60_000}={}) {
  let day=shanghaiDate(now()),lastAttempt=now(),pending=false,running=false;
  async function request(reason='timer') {
    const stamp=now(),nextDay=shanghaiDate(stamp);
    if(nextDay!==day){const previous=day;day=nextDay;pending=true;onDateChange(day,previous);}
    if(!isActive())return false;
    if(reason==='resume'&&stamp-lastAttempt>=2000)pending=true;
    if(!isVisible()||isBusy()||running)return false;
    // Browsers often emit pageshow, focus and visibilitychange together.
    if(reason==='resume'&&stamp-lastAttempt<2000)return false;
    if(!pending&&stamp-lastAttempt<interval)return false;
    pending=false;lastAttempt=stamp;running=true;
    try{await refresh();return true;}finally{running=false;}
  }
  return {tick:()=>request('timer'),resume:()=>request('resume')};
}
