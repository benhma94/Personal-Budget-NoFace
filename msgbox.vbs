' Pops a visible error dialog. Used by run.bat's hidden copy to surface
' fatal startup errors it can no longer echo to a console.
' Usage: wscript.exe msgbox.vbs "<message text>"
MsgBox WScript.Arguments(0), vbCritical + vbOKOnly, "Finance Hub"
