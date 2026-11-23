from pathlib import Path
import json, subprocess, tempfile
kit=Path(__file__).resolve().parents[1]
python=kit/"OverField-Server/runtime-win64/python.exe"
cli=kit/"client_tools/launcher_cli.py"
with tempfile.TemporaryDirectory() as temp:
    home=Path(temp)/'选服 & 本机'
    def call(*args, expected=True):
        output=subprocess.run([str(python),'-B',str(cli),'--home',str(home),*args],capture_output=True,check=False)
        result=json.loads(output.stdout)
        assert (output.returncode==0)==expected and result['ok']==expected and result['apiVersion']=='overfield.launcher/v1',result
        return result.get('result',{})
    assert call('schema')['commands'].keys()=={'schema','status','servers','save','prepare','play'}
    assert not call('status')['prepared'] and call('servers')=={'servers':[]}
    call('save','--mode','gateway','--endpoint','http://127.0.0.1:23845')
    call('save','--mode','ssh','--ssh','ubuntu@example.test:2345')
    assert len(call('servers')['servers'])==2
    call('save','--mode','ssh','--ssh','ubuntu@example.test:2345')
    assert len(call('servers')['servers'])==2
    call('save','--mode','ssh',expected=False)
    call('save','--endpoint','https://outside.example',expected=False)
    call('prepare',expected=False)
    assert not (home/'Client').exists()
print('Launcher CLI discovery, isolated status, SSH and loopback bookmarks, deduplication and input validation passed.')
