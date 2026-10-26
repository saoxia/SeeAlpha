#include <math.h>
#include <stdint.h>
// Columns OHLC, frozen mean target, ATR, unused, signal, previous close.
void run(const double*a,const int64_t*ts,long n,double stopatr,double activate,double lock,
         int hours,double riskfrac,double cap,double*out){
 double bal=500,peak=500,dd=0,win=0,loss=0,q=0,entry=0,stop=0,target=0,best=0;int count=0;int64_t opened=0;
 for(long i=0;i<n;i++){
  const double*r=a+i*9;int exited=0;
  if(q>0){
   double exit=0;
   if(r[1]>=stop)exit=fmax(stop,r[0])*(1.0002);
   else if(r[2]<=target)exit=target*1.0002;
   else if(ts[i]-opened>=(int64_t)hours*3600000000000LL)exit=r[3]*1.0002;
   if(exit==0){
    best=fmin(best,r[2]);
    if((entry-best)/entry>=activate){
     stop=fmin(stop,entry-lock*(entry-best));
     if(r[3]>=stop)exit=r[3]*1.0002;
    }
   }
   if(exit>0){double pnl=q*(entry-exit)-q*.0005*(entry+exit);bal+=pnl;if(pnl>0)win+=pnl;else loss-=pnl;count++;q=0;exited=1;if(bal>peak)peak=bal;if(bal/peak-1<dd)dd=bal/peak-1;}
  }
  if(q==0&&!exited&&r[7]>0&&isfinite(r[4])&&isfinite(r[5])){
   entry=r[0]*.9998;double dist=stopatr*r[5];target=r[4];
   if(target>=entry||dist/entry<.004||dist/entry>.03)continue;
   stop=entry+dist;best=entry;opened=ts[i];
   q=floor(fmin(bal*riskfrac/(dist+.0007*(entry+stop)),bal*cap/entry)*1000)/1000;
   // Entry at next minute open; conservatively allow stop before mean target.
   double exit=0;if(r[1]>=stop)exit=fmax(stop,r[0])*1.0002;else if(r[2]<=target)exit=target*1.0002;
   if(exit>0&&q>0){double pnl=q*(entry-exit)-q*.0005*(entry+exit);bal+=pnl;if(pnl>0)win+=pnl;else loss-=pnl;count++;q=0;if(bal>peak)peak=bal;if(bal/peak-1<dd)dd=bal/peak-1;}
  }
 }
 if(q>0){double exit=a[(n-1)*9+3]*1.0002;double pnl=q*(entry-exit)-q*.0005*(entry+exit);bal+=pnl;if(pnl>0)win+=pnl;else loss-=pnl;count++;if(bal/peak-1<dd)dd=bal/peak-1;}
 out[0]=bal-500;out[1]=count;out[2]=loss>0?win/loss:0;out[3]=dd;
}
