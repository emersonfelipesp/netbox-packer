<?xml version="1.0" encoding="utf-8"?>
<!--
  autounattend.xml — Windows 11 unattended setup for Packer + Proxmox
  ====================================================================
  This file is a Packer templatefile() template.  $${winrm_password} is
  substituted at build time from var.winrm_password (set via
  PKR_VAR_winrm_password env var on the netbox-rq worker).

  ISO prerequisites on Proxmox storage at 10.0.30.71:
    - Windows 11 ISO  : local:iso/Win11_24H2_EnglishInternational_x64.iso
    - virtio-win ISO  : local:iso/virtio-win.iso

  Three install passes:
    windowsPE  — disk layout, VirtIO storage driver, Win11 hardware bypass
    specialize — WinRM enable for Packer communicator, computer name
    oobeSystem — skip OOBE, create build-time local admin, configure autologon
-->
<unattend xmlns="urn:schemas-microsoft-com:unattend"
          xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">

  <!-- ================================================================ -->
  <!-- PASS 1: windowsPE — Windows PE (pre-installation environment)    -->
  <!-- ================================================================ -->
  <settings pass="windowsPE">

    <component name="Microsoft-Windows-International-Core-WinPE"
               processorArchitecture="amd64"
               publicKeyToken="31bf3856ad364e35"
               language="neutral"
               versionScope="nonSxS">
      <SetupUILanguage>
        <UILanguage>en-US</UILanguage>
      </SetupUILanguage>
      <InputLocale>en-US</InputLocale>
      <SystemLocale>en-US</SystemLocale>
      <UILanguage>en-US</UILanguage>
      <UILanguageFallback>en-US</UILanguageFallback>
      <UserLocale>en-US</UserLocale>
    </component>

    <component name="Microsoft-Windows-Setup"
               processorArchitecture="amd64"
               publicKeyToken="31bf3856ad364e35"
               language="neutral"
               versionScope="nonSxS">

      <!--
        Bypass Windows 11 hardware checks for VMs.
        These registry keys skip TPM, Secure Boot, and RAM validation during
        Setup.  Safe even when TPM 2.0 emulation is active — they are no-ops
        when requirements are already satisfied.
      -->
      <RunSynchronous>
        <RunSynchronousCommand wcm:action="add">
          <Order>1</Order>
          <Path>cmd.exe /c reg add "HKLM\SYSTEM\Setup\LabConfig" /v BypassTPMCheck /t REG_DWORD /d 1 /f</Path>
          <WillReboot>Never</WillReboot>
        </RunSynchronousCommand>
        <RunSynchronousCommand wcm:action="add">
          <Order>2</Order>
          <Path>cmd.exe /c reg add "HKLM\SYSTEM\Setup\LabConfig" /v BypassSecureBootCheck /t REG_DWORD /d 1 /f</Path>
          <WillReboot>Never</WillReboot>
        </RunSynchronousCommand>
        <RunSynchronousCommand wcm:action="add">
          <Order>3</Order>
          <Path>cmd.exe /c reg add "HKLM\SYSTEM\Setup\LabConfig" /v BypassRAMCheck /t REG_DWORD /d 1 /f</Path>
          <WillReboot>Never</WillReboot>
        </RunSynchronousCommand>
        <RunSynchronousCommand wcm:action="add">
          <Order>4</Order>
          <Path>cmd.exe /c reg add "HKLM\SYSTEM\Setup\MoSetup" /v AllowUpgradesWithUnsupportedTPMOrCPU /t REG_DWORD /d 1 /f</Path>
          <WillReboot>Never</WillReboot>
        </RunSynchronousCommand>
      </RunSynchronous>

      <!--
        VirtIO storage driver injection.
        Windows PE cannot see a VirtIO disk without the vioscsi/viostor driver.
        Candidate paths cover E:\ and F:\ because the virtio-win ISO drive
        letter varies depending on how many CD-ROMs Packer attaches; Windows
        Setup silently skips paths that do not exist.
      -->
      <DriverPaths>
        <PathAndCredentials wcm:action="add" wcm:keyValue="1">
          <Path>E:\amd64\w11</Path>
        </PathAndCredentials>
        <PathAndCredentials wcm:action="add" wcm:keyValue="2">
          <Path>F:\amd64\w11</Path>
        </PathAndCredentials>
        <PathAndCredentials wcm:action="add" wcm:keyValue="3">
          <Path>E:\vioscsi\w11\amd64</Path>
        </PathAndCredentials>
        <PathAndCredentials wcm:action="add" wcm:keyValue="4">
          <Path>F:\vioscsi\w11\amd64</Path>
        </PathAndCredentials>
      </DriverPaths>

      <UserData>
        <AcceptEula>true</AcceptEula>
        <FullName>N-MultiCloud</FullName>
        <Organization>N-MultiCloud</Organization>
        <!--
          Generic KMS client setup key for Windows 11 Pro.
          Allows unattended installation without a retail key.
          Activate with a per-VM license post-deployment.
        -->
        <ProductKey>
          <Key>VK7JG-NPHTM-C97JM-9MPGT-3V66T</Key>
          <WillShowUI>Never</WillShowUI>
        </ProductKey>
      </UserData>

      <!-- UEFI/GPT disk layout: EFI (100 MB) + MSR (16 MB) + OS (rest) -->
      <DiskConfiguration>
        <WillShowUI>OnError</WillShowUI>
        <Disk wcm:action="add">
          <DiskID>0</DiskID>
          <WillWipeDisk>true</WillWipeDisk>
          <CreatePartitions>
            <CreatePartition wcm:action="add">
              <Order>1</Order>
              <Size>100</Size>
              <Type>EFI</Type>
            </CreatePartition>
            <CreatePartition wcm:action="add">
              <Order>2</Order>
              <Size>16</Size>
              <Type>MSR</Type>
            </CreatePartition>
            <CreatePartition wcm:action="add">
              <Order>3</Order>
              <Extend>true</Extend>
              <Type>Primary</Type>
            </CreatePartition>
          </CreatePartitions>
          <ModifyPartitions>
            <ModifyPartition wcm:action="add">
              <Order>1</Order>
              <PartitionID>1</PartitionID>
              <Format>FAT32</Format>
              <Label>EFI</Label>
            </ModifyPartition>
            <ModifyPartition wcm:action="add">
              <Order>2</Order>
              <PartitionID>3</PartitionID>
              <Format>NTFS</Format>
              <Label>Windows</Label>
              <Letter>C</Letter>
            </ModifyPartition>
          </ModifyPartitions>
        </Disk>
      </DiskConfiguration>

      <ImageInstall>
        <OSImage>
          <WillShowUI>OnError</WillShowUI>
          <InstallTo>
            <DiskID>0</DiskID>
            <PartitionID>3</PartitionID>
          </InstallTo>
        </OSImage>
      </ImageInstall>

    </component>
  </settings>

  <!-- ================================================================ -->
  <!-- PASS 2: specialize — first boot after file copy, before OOBE     -->
  <!-- ================================================================ -->
  <settings pass="specialize">

    <component name="Microsoft-Windows-Shell-Setup"
               processorArchitecture="amd64"
               publicKeyToken="31bf3856ad364e35"
               language="neutral"
               versionScope="nonSxS">
      <!-- cloudbase-init will set the real hostname on first boot from Proxmox metadata -->
      <ComputerName>WIN-PACKER-BUILD</ComputerName>
      <TimeZone>UTC</TimeZone>
    </component>

    <!--
      Enable WinRM (HTTP, port 5985) before OOBE so Packer can connect.
      These commands run as SYSTEM in the specialize pass.
    -->
    <component name="Microsoft-Windows-Deployment"
               processorArchitecture="amd64"
               publicKeyToken="31bf3856ad364e35"
               language="neutral"
               versionScope="nonSxS">
      <RunSynchronous>
        <RunSynchronousCommand wcm:action="add">
          <Order>1</Order>
          <Path>cmd.exe /c sc config WinRM start=auto</Path>
          <WillReboot>Never</WillReboot>
        </RunSynchronousCommand>
        <RunSynchronousCommand wcm:action="add">
          <Order>2</Order>
          <Path>cmd.exe /c winrm quickconfig -q</Path>
          <WillReboot>Never</WillReboot>
        </RunSynchronousCommand>
        <RunSynchronousCommand wcm:action="add">
          <Order>3</Order>
          <Path>cmd.exe /c winrm set winrm/config/service @{AllowUnencrypted="true"}</Path>
          <WillReboot>Never</WillReboot>
        </RunSynchronousCommand>
        <RunSynchronousCommand wcm:action="add">
          <Order>4</Order>
          <Path>cmd.exe /c winrm set winrm/config/service/auth @{Basic="true"}</Path>
          <WillReboot>Never</WillReboot>
        </RunSynchronousCommand>
        <RunSynchronousCommand wcm:action="add">
          <Order>5</Order>
          <Path>cmd.exe /c netsh advfirewall firewall add rule name="WinRM HTTP" protocol=TCP dir=in localport=5985 action=allow</Path>
          <WillReboot>Never</WillReboot>
        </RunSynchronousCommand>
        <RunSynchronousCommand wcm:action="add">
          <Order>6</Order>
          <Path>cmd.exe /c net start WinRM</Path>
          <WillReboot>Never</WillReboot>
        </RunSynchronousCommand>
      </RunSynchronous>
    </component>

  </settings>

  <!-- ================================================================ -->
  <!-- PASS 3: oobeSystem — OOBE (first-user experience)                -->
  <!-- ================================================================ -->
  <settings pass="oobeSystem">

    <component name="Microsoft-Windows-Shell-Setup"
               processorArchitecture="amd64"
               publicKeyToken="31bf3856ad364e35"
               language="neutral"
               versionScope="nonSxS">

      <!-- Skip all OOBE screens -->
      <OOBE>
        <HideEULAPage>true</HideEULAPage>
        <HideLocalAccountScreen>true</HideLocalAccountScreen>
        <HideOEMRegistrationScreen>true</HideOEMRegistrationScreen>
        <HideOnlineAccountScreens>true</HideOnlineAccountScreens>
        <HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE>
        <NetworkLocation>Work</NetworkLocation>
        <ProtectYourPC>3</ProtectYourPC>
        <SkipMachineOOBE>true</SkipMachineOOBE>
        <SkipUserOOBE>true</SkipUserOOBE>
      </OOBE>

      <!--
        Build-time accounts.
        Password comes from var.winrm_password (PKR_VAR_winrm_password env var),
        injected by Packer's templatefile() at build time — never stored in git.

        Both accounts are removed by sysprep at the end of the Packer build
        and do not exist in the resulting template.
      -->
      <UserAccounts>
        <AdministratorPassword>
          <Value>${winrm_password}</Value>
          <PlainText>true</PlainText>
        </AdministratorPassword>
        <LocalAccounts>
          <LocalAccount wcm:action="add">
            <Password>
              <Value>${winrm_password}</Value>
              <PlainText>true</PlainText>
            </Password>
            <DisplayName>packer</DisplayName>
            <Group>Administrators</Group>
            <Name>packer</Name>
          </LocalAccount>
        </LocalAccounts>
      </UserAccounts>

      <!--
        Auto-logon for unattended provisioning over WinRM.
        Sysprep resets AutoLogon count to 0; it is absent from the final template.
      -->
      <AutoLogon>
        <Password>
          <Value>${winrm_password}</Value>
          <PlainText>true</PlainText>
        </Password>
        <Enabled>true</Enabled>
        <LogonCount>5</LogonCount>
        <Username>packer</Username>
      </AutoLogon>

    </component>

  </settings>

</unattend>
