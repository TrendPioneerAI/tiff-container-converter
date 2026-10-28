# V2 / V3 历史授权体系的重建

历史源码保留了原客户端验证公钥，不能使用新生成的私钥直接签发旧公钥认可的许可证。原私钥不会公开在本仓库。如果仍需继续签发原系列许可证，必须自行安全保管原私钥。

如只需要用源码独立建立新的授权体系，在 `v2` 或 `v3` 目录执行以下操作（使用已经安装依赖的 Python）。私钥和许可证应放在仓库外，并由使用者自己备份：

```powershell
python license_admin.py --generate-keys --private-key "D:\safe\tiff-private.pem" --public-key "D:\safe\tiff-public.pem"
python license_admin.py --public-key-b64 --public-key "D:\safe\tiff-public.pem"
```

将第二条输出的公钥字符串替换该版本 `license_core.py` 的 `PUBLIC_KEY_B64`。这是独立授权体系的本地定制，随后可使用以下命令签发许可证：

```powershell
python license_admin.py --private-key "D:\safe\tiff-private.pem" --customer "自用" --expires 2030-12-31 --machine "*" --max-files 0 --output "D:\safe\license.lic"
```

在对应版本客户端安装该许可证后使用。修改公钥后应重新打包客户端；之前由旧私钥签发的许可证不能被新的公钥验证。新建体系不可恢复过去的私钥或旧许可证。
