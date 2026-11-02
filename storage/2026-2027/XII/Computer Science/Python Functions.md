INDIAN SCHOOL AL WADI AL KABIR

Worksheet, 2026-27

Class: XII Comp. Sci. | Department: Computer Science | 

Worksheet No: 1 | Topic: Functions | Note: To be solved in the notebook

1 | Function name must be followed by

2 | keyword is used to define a function

3 | Function will perform its action only when it is

4 | Write statement to call the function. def Add():
X = 10 + 20
print(X)
	 #statement to call the above function

5 | Write statement to call the function. def Add(X,Y):
Z = X+Y
print(Z)
	 #statement to call the above function

6 | Write statement to call the function.

def Add(X,Y):
Z = X+Y
return Z
	#statementtocalltheabovefunction print(“Total =”,C)

7 | Which Line Number Code will never execute?
def Check(num):	#Line 1 if
num%2==0:	#Line 2
print("Hello")	#Line 3
return True	#Line 4
print("Bye")	#Line 5
else:	#Line 6
return False	#Line 7
C=Check(20) print(C)

8 | What will be the output of following code?
def Cube(n):
print(n*n*n)

Cube(n)	# n is 10 here print(Cube(n))

9 | What will be the output of the following Python code? 
age = 5 
def increase(): 
     global age 
     age = age + 10 
def start(): 
     age = 10 
     increase() 
     print("Inside start:", age) 
start() 
print("Outside:", age)

10 | What will be the output?

def fun1(x, y):
x = x + y y = x – y x = x – y
print(‘a=’,x)
print(‘b=’,y)
a = 5
b = 3 fun1(a,b)

11 | What will be the output of the following Python code?
i = 5
print(i,end='@@')
def add():
     global i
      i = i+7
      print(i,end='##')
add()
 print(i)

12 | What will be the output?

def func(b):
global x print(‘Global x=’, x)

 | y = x + b x = 7
z = x – b print(‘Local x = ‘,x) print(‘y = ‘,y)
print(‘z = ‘,z)
x=5 func(10)

13. | What will be the output?
a = 15
def update(x):
     global a
     a += 2
     if x%2==0:
            a *= x
     else:
            a //= x
a=a+5
print(a, end="$")
update(5)
print(a)

14 | Write a function, Words(S), that takes a string as an argument and returns a list containing words of the string that has consonants.

15 | Write the definition of a function Reverse (x) in Python, to display the elements in reverse order such that each
displayed element is the twice of the original element (element *2) of the List x in the following manner: Example :
If List x contains 7 integers is as follows:
4 8 7 5 6 2 10
After executing the function, the array content should be displayed as follows: 20 4 12 10 14 16 8

16 | Write a Python function topper() that accepts a dictionary resultsheet that contains student details { rollno: [ name,mark]}. The function should return the name and rollno of the student who scored highest mark. 
Display as “Topper is xyz with rollno abc”.

17. | Write a function remove_duplicates() in Python that accepts a list L and returns a new list containing only the  
 unique elements from L, after removing the duplicates

18. | Write the definition of a function AddPrev(A, N) in Python, which should add every previous value of list A to the next value and assign the sum at the index of the next value. The list A contains N number of integers. The function should finally display the entire content of the changed list. 
Example : If the list A contains the following 10 elements (i.e. for N=10).
  0         1         2          3        4         5         6      7        8      9

Then the function should display the output as follows :
9 # 14 # 29 # 39 # 64 # 76 # 81 # 90 # 95 # 107 #