#include <math.h>
#include <stdint.h>
// Columns: open,high,low,close,mean,std,atr,allowed,previous_close.
// Quote features are lagged to a completed 5m bar before the current source minute.
void run_dynamic(const double *a, const int64_t *ts, long n, double z,double edge,double stopatr,
         double fraction,int hours,int cooldown,double riskfrac,double cap,double *out){
 double bal=500,peak=500,dd=0,win=0,loss=0,q=0,entry=0,stop=0,target=0;
 int count=0;int64_t opened=0,ready=0;
 for(long i=0;i<n;i++){
  const double *r=a+i*9;int exited=0;
  if(q>0){
   if(isfinite(r[4]))target=fmin(entry*(1.-fraction),r[4]);
   double exit=0,fee=.0002;int stophit=0;
   if(r[1]>=stop){exit=fmax(stop,r[0]);fee=.0005;stophit=1;}
   else if(r[2]<=target)exit=target;
   else if(ts[i]-opened>=(int64_t)hours*3600000000000LL){exit=r[3];fee=.0005;}
   if(exit>0){
    double pnl=q*(entry-exit)-q*(entry*(.0002+.0002)+exit*(fee+.0002));
    bal+=pnl;if(pnl>0)win+=pnl;else loss-=pnl;count++;q=0;exited=1;
    ready=ts[i]+(int64_t)(stophit?cooldown:5)*60000000000LL;
    if(bal>peak)peak=bal;if(bal/peak-1<dd)dd=bal/peak-1;
   }
  }
  if(q==0&&!exited&&ts[i]>=ready&&r[7]>0&&isfinite(r[4])&&isfinite(r[5])&&isfinite(r[6])){
   double limit=r[4]+z*r[5];double dist=stopatr<0?-stopatr*limit:stopatr*r[6];
   if(limit<=r[8]||limit/r[4]-1<edge||dist/limit<.004||dist/limit>.03)continue;
   if(r[1]<limit)continue;
   entry=limit;stop=entry+dist;target=fmin(entry*(1.-fraction),r[4]);
   q=floor(fmin(bal*riskfrac/(dist+.0007*(entry+stop)),bal*cap/entry)*1000)/1000;
   opened=ts[i];
   // Conservative: allow an entry-bar stop, never credit an entry-bar target.
   if(q>0&&r[1]>=stop){
    double exit=fmax(stop,r[0]);double pnl=q*(entry-exit)-q*(entry*.0004+exit*.0007);
    bal+=pnl;if(pnl>0)win+=pnl;else loss-=pnl;count++;q=0;
    ready=ts[i]+(int64_t)cooldown*60000000000LL;
    if(bal>peak)peak=bal;if(bal/peak-1<dd)dd=bal/peak-1;
   }
  }
 }
 if(q>0){double exit=a[(n-1)*9+3];double pnl=q*(entry-exit)-q*(entry*.0004+exit*.0007);bal+=pnl;if(pnl>0)win+=pnl;else loss-=pnl;count++;if(bal/peak-1<dd)dd=bal/peak-1;}
 out[0]=bal-500;out[1]=count;out[2]=loss>0?win/loss:0;out[3]=dd;
}
