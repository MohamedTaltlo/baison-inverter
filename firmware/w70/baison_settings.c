/*
 * BAISON V12 validated Axpert/Voltronic VMIII settings command vocabulary.
 * Faithfully mirrors the allowlisted specifications in upstream settings.py.
 * Values outside the defined options and ranges are blocked.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
typedef struct {
    const char*prefix;
    int type;
    int min,max;
    const char*choices;
    int readback_index;
} setting_rule;
#define CHOICE 1
#define VOLTAGE 2
#define CURRENT 3
static const setting_rule rules[]={
 {"POP",CHOICE,0,2,"012",16},
 {"PCP",CHOICE,1,3,"123",17},
 {"PGR",CHOICE,0,1,"01",15},
 {"PBT",CHOICE,0,9,"0123456789",12},
 {"PBCV",VOLTAGE,220,280,"",8},
 {"PBDV",VOLTAGE,240,300,"",22},
 {"PSDV",VOLTAGE,210,250,"",9},
 {"PCVV",VOLTAGE,250,315,"",10},
 {"PBFT",VOLTAGE,250,315,"",11},
 {"MNCHGC",CURRENT,10,120,"",14},
 {"MUCHGC",CURRENT,2,120,"",13},
 {"F",CHOICE,0,0,"",3},
 {"V",CHOICE,0,0,"",2},
 {"PSPB",CHOICE,0,1,"01",24},
 {"PPVOKC",CHOICE,0,1,"01",23}
};
static int digits(const char*txt,unsigned n) {
    unsigned i;
    if(!n)return 0;
    for(i=0;i<n;i++)if(txt[i]<'0'||txt[i]>'9')return 0;
    return 1;
}
int baison_setting_rule(const char *cmd,const char **readback,int *index) {
    unsigned i,j,n;
    if(!cmd)return 0;
    if(strcmp(cmd,"PF")==0) { if(readback)*readback="QPIRI"; if(index)*index=-2; return 1; }
    n=(unsigned)strlen(cmd);
    if(n<3||n>36)return 0;
    /* Flags from settings.py, lower-case letters are intentional. */
    if(n==3 && (cmd[0]=='P') && (cmd[1]=='E'||cmd[1]=='D') &&
       strchr("abkuvxyz",cmd[2])!=NULL) {
        if(readback)*readback="QFLAG";
        if(index)*index=-1;
        return 1;
    }
    for(i=0;i<sizeof(rules)/sizeof(rules[0]);i++) {
        const setting_rule *r=rules+i;
        unsigned p=(unsigned)strlen(r->prefix),sl;
        const char *suffix;
        int value=0;
        if(n<=p||strncmp(cmd,r->prefix,p)!=0)continue;
        suffix=cmd+p;sl=n-p;
        if(r->type==CHOICE) {
            if(strcmp(r->prefix,"F")==0) {
                if(strcmp(suffix,"50") && strcmp(suffix,"60"))return 0;
            } else if(strcmp(r->prefix,"V")==0) {
                if(strcmp(suffix,"220") && strcmp(suffix,"230") && strcmp(suffix,"240"))return 0;
            } else {
                if(sl!=2 || suffix[0]!='0' || strchr(r->choices,suffix[1])==NULL)return 0;
            }
        } else if(r->type==VOLTAGE) {
            if(sl!=4 || !digits(suffix,2)||suffix[2]!='.'||!digits(suffix+3,1))return 0;
            value=((suffix[0]-'0')*10+suffix[1]-'0')*10+(suffix[3]-'0');
            if(value<r->min||value>r->max)return 0;
        } else {
            if(sl!=3 || !digits(suffix,3))return 0;
            for(j=0;j<sl;j++)value=value*10+suffix[j]-'0';
            if(value<r->min || value>r->max)return 0;
        }
        if(readback)*readback="QPIRI";
        if(index)*index=r->readback_index;
        return 1;
    }
    return 0;
}


/* Check the actual current setting returned by QPIRI or QFLAG, not merely
 * the ACK from the inverter. Return 1 verified, 0 mismatched, -1 invalid. */
int baison_setting_verify(const char *cmd,const char *readback) {
    const char *query=NULL;
    const setting_rule *rule=NULL;
    int idx=-1;
    unsigned i,p=0,current=0;
    char token[64];
    const char *suffix=NULL;
    char *end;
    double actual,wanted,diff;
    if(!readback||!baison_setting_rule(cmd,&query,&idx))return -1;
    /* PF acknowledges a factory reset; firmware cannot prove all default
       settings from a single QPIRI response, so report unverified. */
    if(strcmp(cmd,"PF")==0)return 0;
    if(idx<0) {
        const char *sep;
        const char *start;
        const char *stop;
        char flag=cmd[2];
        if(readback[0]!='E')return 0;
        sep=strchr(readback,'D');
        if(!sep)return 0;
        if(cmd[1]=='E'){start=readback+1;stop=sep;}
        else {start=sep+1;stop=readback+strlen(readback);}
        for(;start<stop;start++)if(*start==flag)return 1;
        return 0;
    }
    for(i=0;i<sizeof(rules)/sizeof(rules[0]);i++){
        unsigned length=(unsigned)strlen(rules[i].prefix);
        if(strncmp(cmd,rules[i].prefix,length)==0) {
            rule=rules+i;suffix=cmd+length;break;
        }
    }
    if(!rule||!suffix)return -1;
    for(i=0;readback[i];) {
        unsigned n=0;
        while(readback[i]==' '||readback[i]=='\t')i++;
        if(!readback[i])break;
        while(readback[i]&&readback[i]!=' '&&readback[i]!='\t'){
            if(n+1<sizeof token)token[n++]=readback[i];
            i++;
        }
        token[n]=0;
        if((int)current==idx){
            actual=strtod(token,&end);
            if(end==token||*end)return 0;
            wanted=strtod(suffix,&end);
            if(end==suffix||*end)return 0;
            diff=actual-wanted;
            return diff>-0.051 && diff<0.051;
        }
        current++;
    }
    (void)p;
    return 0;
}
