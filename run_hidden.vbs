' Runs "<path>" [args...] completely hidden (no console window), without
' waiting for it to finish. Used by run.bat to relaunch itself invisibly:
' Explorer always shows a console for a split second when a .bat is
' double-clicked (that's how Windows starts it, before any of the batch
' file's own commands run) — this hands off to a hidden copy immediately
' so nothing lingers after that unavoidable flash.
' Usage: wscript.exe run_hidden.vbs "<path>" [arg1] [arg2] ...
Dim cmd, i
cmd = """" & WScript.Arguments(0) & """"
For i = 1 To WScript.Arguments.Count - 1
    cmd = cmd & " """ & WScript.Arguments(i) & """"
Next
CreateObject("WScript.Shell").Run cmd, 0, False
