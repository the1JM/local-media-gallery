set selfPath to POSIX path of (path to me)
if selfPath ends with ".app/" or selfPath ends with ".app" then
	set launcherDir to do shell script "dirname " & quoted form of selfPath
	set projectDir to do shell script "cd " & quoted form of (launcherDir & "/../Projects/Gallery App") & " && pwd"
else
	set scriptDir to do shell script "dirname " & quoted form of selfPath
	set projectDir to do shell script "cd " & quoted form of (scriptDir & "/..") & " && pwd"
end if
do shell script quoted form of (projectDir & "/Open Screenshot Gallery.command")
