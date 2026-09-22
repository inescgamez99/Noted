Dim dir
Dim fso : Set fso = CreateObject("Scripting.FileSystemObject")
' La raiz del repo: este .vbs vive en scripts/, watchdog.ps1 en la raiz.
dir = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
CreateObject("WScript.Shell").Run "powershell.exe -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & dir & "\watchdog.ps1""", 0, False
