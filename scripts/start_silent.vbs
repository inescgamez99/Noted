Set fso = CreateObject("Scripting.FileSystemObject")
Set WshShell = CreateObject("WScript.Shell")
' La raiz del repo: este .vbs vive en scripts/, main.py en la raiz.
Dim dir : dir = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
WshShell.Run "pythonw """ & dir & "\main.py""", 0, False
