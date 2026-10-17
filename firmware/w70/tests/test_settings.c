#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int baison_setting_rule(const char*,const char**,int*);
int baison_setting_verify(const char*,const char*);

#define CHECK(expr) do{if(!(expr)){fprintf(stderr,"FAILED line %d: %s\n",__LINE__,#expr);return 1;}}while(0)

int main(void){
 const char *qpiri="220.0 19.0 220.0 50.0 19.0 4200 4200 24.0 23.0 21.0 29.2 27.0 2 010 010 0 0 2 1 01 0 0 27.0 0 1";
 const char*query;int index;
 CHECK(baison_setting_rule("POP00",&query,&index)==1 && index==16);
 CHECK(baison_setting_rule("PGR00",&query,&index)==1 && index==15);
 CHECK(baison_setting_rule("MUCHGC010",&query,&index)==1 && index==13);
 CHECK(baison_setting_verify("POP00",qpiri)==1);
 CHECK(baison_setting_verify("POP01",qpiri)==0);
 CHECK(baison_setting_verify("PGR00",qpiri)==1);
 CHECK(baison_setting_verify("PCP02",qpiri)==1);
 CHECK(baison_setting_verify("PBCV23.0",qpiri)==1);
 CHECK(baison_setting_verify("MUCHGC010",qpiri)==1);
 CHECK(baison_setting_verify("PEa","EabkDxyz")==1);
 CHECK(baison_setting_verify("PDa","EabkDxyz")==0);
 CHECK(baison_setting_verify("PDy","EabkDxyz")==1);
 CHECK(baison_setting_rule("POP99",NULL,NULL)==0);
 CHECK(baison_setting_rule("PBT99",NULL,NULL)==0);
 CHECK(baison_setting_rule("PCVV40.0",NULL,NULL)==0);
 puts("PASS: PI30 settings validation and actual QPIRI / QFLAG readback.");
 return 0;
}
