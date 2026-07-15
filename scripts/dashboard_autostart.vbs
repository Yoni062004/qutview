' Starts the QUTVIEW dashboard silently in the background (no window),
' so http://localhost:8501 works without opening a terminal first.
' A shortcut to this script in the Windows Startup folder makes the
' dashboard available automatically after every login. Output goes to
' logs\dashboard.log. Safe to run twice: if the port is already served,
' the second server just fails to bind and exits.
Dim fso, shell, root
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
shell.Run "cmd /c cd /d """ & root & """ && (if not exist logs mkdir logs) && " & _
    """.venv\Scripts\python.exe"" -m streamlit run app\dashboard.py " & _
    "--server.headless true --server.port 8501 >> logs\dashboard.log 2>&1", 0, False
