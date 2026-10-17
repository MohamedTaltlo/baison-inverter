#!/usr/bin/env python3
"""Reconstruct C literal HTML, validate Arabic UI and syntax-check embedded JS."""
import ast
import re
import subprocess
from pathlib import Path

ROOT = Path("firmware/w70")
CASES = [
    ("baison_ui.c","baison_home_page",["إعداد شبكة Wi-Fi","القراءات الحية","scan(true)"]),
    ("baison_settings_ui.c","baison_settings_page",["إعدادات الإنفرتر","القيمة الحالية","verified"]),
    ("baison_recovery.c","recovery_page",["تحديث برنامج القطعة","/api/firmware"]),
]
for path,name,mandatory in CASES:
    data=(ROOT/path).read_text("utf-8")
    marker="const char "+name+"[] ="
    assert marker in data,(path,"missing literal")
    lines=data[data.index(marker):].splitlines()[1:]
    parts=[]
    for line in lines:
        literal=line.strip()
        done=literal.endswith(";")
        if done:literal=literal[:-1]
        if not literal.startswith('"'):
            raise ValueError((path,"unexpected C initializer",literal[:80]))
        parts.append(ast.literal_eval(literal))
        if done:break
    text="".join(parts)
    assert text.startswith("<!doctype html>"),(path,text[:80])
    assert '<html lang="ar" dir="rtl">' in text
    assert ":light" in text
    assert 'id="pin"' not in text and 'رمز التعديل' not in text, f"{path}: unexpected PIN field"
    assert not re.search(r"\bV\d{1,3}\b",text), f"{path}: visible firmware version"
    for fragment in mandatory:
        assert fragment in text, (path,fragment)
    scripts=re.findall(r"<script>(.*?)</script>",text,re.S)
    assert scripts, (path,"missing JS")
    for js in scripts:
        subprocess.run(["node","--check","-"],input=js,text=True,check=True)
    print("PASS",path,"HTML",len(text),"bytes; JavaScript syntax OK")
