param([string]$HandleFile)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class FixtureVisibility { [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr handle, int command); }'
$form = New-Object System.Windows.Forms.Form
$form.Text = 'OmniTest Desktop Fixture'
$form.Width = 420
$form.Height = 230
$inputBox = New-Object System.Windows.Forms.TextBox
$inputBox.Name = 'message'
$inputBox.AccessibleName = 'Message'
$inputBox.SetBounds(30, 30, 340, 30)
$button = New-Object System.Windows.Forms.Button
$button.Name = 'apply'
$button.Text = 'Apply'
$button.SetBounds(30, 80, 110, 30)
$result = New-Object System.Windows.Forms.Label
$result.Name = 'result'
$result.Text = 'Ready'
$result.SetBounds(30, 130, 340, 30)
$button.Add_Click({ $result.Text = $inputBox.Text.ToUpperInvariant() })
$form.Controls.AddRange(@($inputBox, $button, $result))
$form.Add_Shown({
    [FixtureVisibility]::ShowWindow($form.Handle, 5) | Out-Null
    if ($HandleFile) { [System.IO.File]::WriteAllText($HandleFile, $form.Handle.ToInt64().ToString()) }
})
[System.Windows.Forms.Application]::Run($form)
