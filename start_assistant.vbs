Option Explicit

Dim shell, fso, appRoot, appPath, localPython, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appRoot = fso.GetParentFolderName(WScript.ScriptFullName)
appPath = fso.BuildPath(appRoot, "launch_assistant.pyw")
localPython = fso.BuildPath(shell.ExpandEnvironmentStrings("%LOCALAPPDATA%"), "DenoiseAssistant\toolchains\python3146\pythonw.exe")

On Error Resume Next
If fso.FileExists(localPython) Then
    command = Chr(34) & localPython & Chr(34) & " " & Chr(34) & appPath & Chr(34)
    shell.Run command, 0, False
    If Err.Number <> 0 Then
        Err.Clear
        localPython = ""
    End If
Else
    localPython = ""
End If
If localPython = "" Then
    command = "pythonw.exe " & Chr(34) & appPath & Chr(34)
    shell.Run command, 0, False
    If Err.Number <> 0 Then
        Err.Clear
        command = "pyw.exe -3 " & Chr(34) & appPath & Chr(34)
        shell.Run command, 0, False
    End If
End If
If Err.Number <> 0 Then
    MsgBox "The Denoise Assistant runtime could not start. Python 3 was not found.", 16, "Denoise Assistant"
End If
On Error GoTo 0